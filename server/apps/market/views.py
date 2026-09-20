from django.conf import settings
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assets.models import Asset

from .services import get_fx, refresh_quote_map


class QuoteView(APIView):
    """批量行情。默认走缓存，refresh=1 强制抓取。

    抓取与落库都交给 `services.refresh_quote_map()`（唯一一处抓行情并写 PriceQuote 的
    代码），这里只负责拼响应 —— 缓存判据写两份就一定会漂。
    顺便：它也是**批量**取价的 —— 一次 asset_ids 里的 A/港/美标的合成一条腾讯请求，
    而不是一只一条（见 `services.fetch_quotes`）。
    """

    def get(self, request):
        raw = request.query_params.get("asset_ids", "")
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        force = request.query_params.get("refresh") in ("1", "true")
        if not ids:
            return Response({"results": []})

        assets = list(Asset.objects.filter(id__in=ids))
        refreshed, _requests, _batches = refresh_quote_map(assets, force=force)

        result = []
        for asset in assets:
            one = refreshed[asset.id]
            quote = one.quote
            if not quote:
                result.append({"asset_id": asset.id, "symbol": asset.symbol, "name": asset.name, "price": None, "stale": True, "source": ""})
                continue
            result.append(
                {
                    "asset_id": asset.id,
                    "symbol": asset.symbol,
                    "name": asset.name,
                    "price": str(quote.price),
                    "currency": quote.currency,
                    "change_pct": str(quote.change_pct) if quote.change_pct is not None else None,
                    "fetched_at": quote.fetched_at.isoformat(),
                    "source": quote.source,
                    "stale": one.stale,
                }
            )
        return Response({"results": result})


class FxView(APIView):
    """汇率查询，按日缓存。

    缓存判据、出网、落库都在 `services.get_fx()`（**唯一一处**读写汇率表的代码）。
    以前这里自带一份「查最近一条 → 出网 → 写库」，与 `analytics/services.get_rate`
    那一份的判据还不一样（一个看「是不是当天的」，一个只看「有没有记录」），
    于是同一个页面上的两个汇率可以不是同一个数。这里只负责拼响应。
    """

    def get(self, request):
        base = (request.query_params.get("base") or "USD").upper()
        quote = (request.query_params.get("quote") or settings.BASE_CURRENCY).upper()
        lookup = get_fx(base, quote)
        if lookup.rate is None:
            # 既没有旧记录、这一轮也没抓到 —— 唯一一种给不出汇率的情形
            return Response({"detail": "汇率源不可用"}, status=status.HTTP_502_BAD_GATEWAY)
        body = {
            "base": base,
            "quote": quote,
            "rate": str(lookup.rate),
            # 同币种恒等时没有日期可言，给 null 而不是假装今天
            "date": lookup.date.isoformat() if lookup.date else None,
            # 实际给价的那一家（`fx_source.FX_SOURCE_NAMES` 里的名字）。
            # 以前这里写死成第一家的名字，备用源顶上来时回的是错的名字。
            "source": lookup.source,
        }
        if lookup.stale:
            # 交出去的是上一次的价 —— 与行情那边一样，标明它旧
            body["stale"] = True
        return Response(body)
