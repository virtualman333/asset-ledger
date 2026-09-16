from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.accounts.models import Account
from apps.assets.models import Asset
from apps.transactions.models import Transaction
from apps.transactions.serializers import TransactionSerializer

from .models import Evidence, IngestJob
from .services import extract_from_image, extract_from_text


def _serialize_job(job: IngestJob) -> dict:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "confidence": job.confidence,
        "missing": job.missing,
        "result": job.result_json,
        "error": job.error,
        "evidence_id": job.evidence_id,
        "evidence_url": job.evidence.file.url if job.evidence_id and job.evidence.file else None,
        "raw_text": job.raw_text,
        "confirmed_at": job.confirmed_at.isoformat() if job.confirmed_at else None,
        "created_at": job.created_at.isoformat(),
    }


def _run_extraction(user, kind, *, evidence=None, text="", client_request_id=None) -> IngestJob:
    job = IngestJob.objects.create(
        user=user,
        kind=kind,
        evidence=evidence,
        raw_text=text or "",
        status=IngestJob.Status.PENDING,
        client_request_id=client_request_id,
    )
    try:
        if kind == IngestJob.Kind.IMAGE and evidence and evidence.file:
            result, provider = extract_from_image(evidence.file.path)
        else:
            result, provider = extract_from_text(text)
        job.result_json = {**result, "provider": provider}
        job.confidence = float(result.get("confidence") or 0)
        job.missing = result.get("missing") or []
        job.status = IngestJob.Status.SUCCEEDED if result.get("side") else IngestJob.Status.FAILED
        if not result.get("side"):
            job.error = "未能识别出交易方向，请手动录入"
    except Exception as exc:  # 识别失败不能影响记账主流程
        job.status = IngestJob.Status.FAILED
        job.error = str(exc)
    job.save()
    return job


def _reuse_job(user, client_request_id):
    """同一幂等键重复提交时直接返回已有草稿，不重复识别。"""
    if not client_request_id:
        return None
    return IngestJob.objects.filter(user=user, client_request_id=client_request_id).first()


class IngestImageView(APIView):
    """上传截图 → 抽取为草稿。"""

    def post(self, request):
        reused = _reuse_job(request.user, request.data.get("client_request_id"))
        if reused:
            return Response(_serialize_job(reused))
        file = request.FILES.get("file")
        if not file:
            return Response({"detail": "缺少 file"}, status=status.HTTP_400_BAD_REQUEST)
        evidence = Evidence.objects.create(user=request.user, file=file)
        job = _run_extraction(
            request.user,
            IngestJob.Kind.IMAGE,
            evidence=evidence,
            client_request_id=request.data.get("client_request_id"),
        )
        return Response(_serialize_job(job), status=status.HTTP_201_CREATED)


class IngestTextView(APIView):
    """提交文本 → 抽取为草稿。"""

    def post(self, request):
        reused = _reuse_job(request.user, request.data.get("client_request_id"))
        if reused:
            return Response(_serialize_job(reused))
        text = (request.data.get("text") or "").strip()
        if not text:
            return Response({"detail": "缺少 text"}, status=status.HTTP_400_BAD_REQUEST)
        evidence = Evidence.objects.create(user=request.user, ocr_text=text)
        job = _run_extraction(
            request.user,
            IngestJob.Kind.TEXT,
            text=text,
            evidence=evidence,
            client_request_id=request.data.get("client_request_id"),
        )
        return Response(_serialize_job(job), status=status.HTTP_201_CREATED)


class JobDetailView(APIView):
    def get(self, request, pk: int):
        job = IngestJob.objects.filter(user=request.user, pk=pk).first()
        if not job:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(_serialize_job(job))


class DraftListView(APIView):
    """草稿箱：识别成功但未确认入账的任务。"""

    def get(self, request):
        jobs = IngestJob.objects.filter(
            user=request.user, confirmed_at__isnull=True, status=IngestJob.Status.SUCCEEDED
        ).select_related("evidence")
        return Response({"results": [_serialize_job(j) for j in jobs]})


class ConfirmView(APIView):
    """确认入账。AI 结果一律可改，改完才写流水；凭证永久关联。"""

    def post(self, request, pk: int):
        job = IngestJob.objects.filter(
            user=request.user, pk=pk, confirmed_at__isnull=True, status=IngestJob.Status.SUCCEEDED
        ).first()
        if not job:
            return Response({"detail": "草稿不存在或已入账"}, status=status.HTTP_404_NOT_FOUND)

        data = dict(job.result_json or {})
        data.update({k: v for k, v in (request.data.get("overrides") or {}).items()})

        symbol = data.get("symbol")
        market = data.get("market") or "OTHER"
        if not symbol:
            return Response({"detail": "缺少标的代码，请先补充"}, status=status.HTTP_400_BAD_REQUEST)

        asset_id = request.data.get("asset_id")
        asset = None
        if asset_id:
            asset = Asset.objects.filter(id=asset_id).first()
        if not asset:
            asset, _ = Asset.objects.get_or_create(
                market=market,
                symbol=symbol,
                defaults={"name": data.get("name") or symbol, "currency": data.get("currency") or "CNY"},
            )

        account = None
        if request.data.get("account_id"):
            account = Account.objects.filter(user=request.user, id=request.data["account_id"]).first()
        else:
            hint = (data.get("account_hint") or "").strip()
            if hint:
                account = Account.objects.filter(user=request.user, name__icontains=hint).first()
            if not account:
                account = Account.objects.filter(user=request.user).first()
        if not account:
            return Response({"detail": "请先创建账户"}, status=status.HTTP_400_BAD_REQUEST)

        traded_at = data.get("traded_at")
        if not traded_at:
            traded_at = timezone.now().isoformat()

        payload = {
            "account": account.id,
            "asset": asset.id,
            "side": data.get("side"),
            "quantity": data.get("quantity"),
            "price": data.get("price"),
            # 金额必须透传：出入金没有数量×单价可推，漏掉这一行就等于把用户
            # 在草稿里（或 overrides 里）补的金额直接丢掉。
            "amount": data.get("amount"),
            "fee": data.get("fee") or 0,
            "tax": data.get("tax") or 0,
            "currency": data.get("currency") or asset.currency,
            "traded_at": traded_at,
            "source": "AGENT",
            "evidence": job.evidence_id,
            "client_request_id": f"ingest-{job.id}",
            "note": f"Agent 识别自凭证 #{job.evidence_id}",
        }
        serializer = TransactionSerializer(data=payload, context={"request": request})
        serializer.is_valid(raise_exception=True)
        tx = serializer.save()

        job.confirmed_at = timezone.now()
        job.save(update_fields=["confirmed_at"])
        return Response(TransactionSerializer(tx).data, status=status.HTTP_201_CREATED)


class DiscardView(APIView):
    """丢弃草稿，原始凭证仍保留。"""

    def post(self, request, pk: int):
        job = IngestJob.objects.filter(user=request.user, pk=pk, confirmed_at__isnull=True).first()
        if not job:
            return Response({"detail": "not found"}, status=status.HTTP_404_NOT_FOUND)
        job.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
