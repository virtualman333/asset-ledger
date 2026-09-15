from datetime import timedelta

from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.assets.models import Asset

from .models import FxRate, PriceQuote
from .services import fetch_fx, fetch_quote


class QuoteView(APIView):
    """批量行情。默认走缓存，refresh=1 强制抓取。"""

    def get(self, request):
        raw = request.query_params.get("asset_ids", "")
        ids = [int(x) for x in raw.split(",") if x.strip().isdigit()]
        force = request.query_params.get("refresh") in ("1", "true")
        if not ids:
            return Response({"results": []})

        cache_cut = timezone.now() - timedelta(seconds=settings.QUOTE_CACHE_SECONDS)
        assets = Asset.objects.filter(id__in=ids)
        result = []
        for asset in assets:
            latest = PriceQuote.objects.filter(asset=asset).order_by("-fetched_at").first()
            quote = latest
            stale = False
            if force or not latest or latest.fetched_at < cache_cut:
                data = fetch_quote(asset)
                if data:
                    quote = PriceQuote.objects.create(
                        asset=asset,
                        price=data["price"],
                        currency=data.get("currency") or asset.currency,
                        change_pct=data.get("change_pct"),
                        source=data.get("source", ""),
                    )
                else:
                    stale = True
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
                    "stale": stale,
                }
            )
        return Response({"results": result})


class FxView(APIView):
    """汇率查询，按日缓存。"""

    def get(self, request):
        base = (request.query_params.get("base") or "USD").upper()
        quote = (request.query_params.get("quote") or settings.BASE_CURRENCY).upper()
        if base == quote:
            return Response({"base": base, "quote": quote, "rate": "1", "date": timezone.localdate().isoformat()})

        row = FxRate.objects.filter(base=base, quote=quote).order_by("-date").first()
        if row and row.date >= timezone.localdate():
            return Response({"base": base, "quote": quote, "rate": str(row.rate), "date": row.date.isoformat(), "source": row.source})

        rate = fetch_fx(base, quote)
        if rate is None:
            if row:
                return Response({"base": base, "quote": quote, "rate": str(row.rate), "date": row.date.isoformat(), "stale": True})
            return Response({"detail": "汇率源不可用"}, status=status.HTTP_502_BAD_GATEWAY)
        row = FxRate.objects.update_or_create(
            base=base, quote=quote, date=timezone.localdate(), defaults={"rate": rate, "source": "frankfurter"}
        )[0]
        return Response({"base": base, "quote": quote, "rate": str(row.rate), "date": row.date.isoformat(), "source": row.source})
