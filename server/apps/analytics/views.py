from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.transactions.models import DividendRecord

from .services import build_positions, build_summary, dividend_monthly


class PositionsView(APIView):
    def get(self, request):
        return Response({"results": build_positions(request.user)})


class SummaryView(APIView):
    def get(self, request):
        base = request.query_params.get("base")
        return Response(build_summary(request.user, base))


class DividendAnalyticsView(APIView):
    def get(self, request):
        year = request.query_params.get("year") or timezone.localdate().year
        return Response({"year": int(year), "months": dividend_monthly(request.user, int(year))})


class CalendarView(APIView):
    """股息日历：除权日/派息日提醒。"""

    def get(self, request):
        rows = (
            DividendRecord.objects.filter(user=request.user)
            .select_related("asset")
            .order_by("pay_date")
        )
        data = [
            {
                "id": row.id,
                "asset": row.asset.symbol,
                "asset_name": row.asset.name,
                "ex_date": row.ex_date.isoformat() if row.ex_date else None,
                "pay_date": row.pay_date.isoformat() if row.pay_date else None,
                "net": str(row.net),
                "gross": str(row.gross),
                "currency": row.currency,
                "reinvested": row.reinvested,
            }
            for row in rows
        ]
        return Response({"results": data})
