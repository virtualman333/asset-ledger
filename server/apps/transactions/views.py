from rest_framework import viewsets

from .models import DividendRecord, Transaction
from .serializers import DividendRecordSerializer, TransactionSerializer


class TransactionViewSet(viewsets.ModelViewSet):
    serializer_class = TransactionSerializer
    filterset_fields = ("account", "asset", "side", "currency", "source")
    search_fields = ("note", "asset__symbol", "asset__name")
    ordering_fields = ("traded_at", "amount", "created_at")

    def get_queryset(self):
        qs = Transaction.objects.filter(user=self.request.user).select_related("asset", "account")
        from_date = self.request.query_params.get("from")
        to_date = self.request.query_params.get("to")
        if from_date:
            qs = qs.filter(traded_at__date__gte=from_date)
        if to_date:
            qs = qs.filter(traded_at__date__lte=to_date)
        return qs


class DividendRecordViewSet(viewsets.ModelViewSet):
    serializer_class = DividendRecordSerializer
    filterset_fields = ("asset", "account", "currency", "reinvested")
    search_fields = ("asset__symbol", "asset__name")
    ordering_fields = ("pay_date", "ex_date", "net")

    def get_queryset(self):
        qs = DividendRecord.objects.filter(user=self.request.user).select_related("asset", "account")
        year = self.request.query_params.get("year")
        if year:
            qs = qs.filter(pay_date__year=year)
        return qs
