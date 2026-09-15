from rest_framework import viewsets

from .models import Account
from .serializers import AccountSerializer


class AccountViewSet(viewsets.ModelViewSet):
    """账户增删改查，天然按当前用户隔离。"""

    serializer_class = AccountSerializer
    filterset_fields = ("market", "account_type", "currency")
    search_fields = ("name", "broker")
    ordering_fields = ("created_at", "name")

    def get_queryset(self):
        return Account.objects.filter(user=self.request.user)

    def perform_destroy(self, instance):
        # 有流水的账户不允许删除，避免账目失联
        if instance.transactions.exists():
            from rest_framework.exceptions import ValidationError

            raise ValidationError("该账户下已有流水，不能删除")
        instance.delete()
