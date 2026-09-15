from decimal import Decimal

from rest_framework import serializers

from .models import DividendRecord, Transaction


class TransactionSerializer(serializers.ModelSerializer):
    """流水序列化。未提供 amount 时按 side 自动推导。"""

    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Transaction
        fields = (
            "id", "user", "account", "asset", "side", "quantity", "price", "amount",
            "fee", "tax", "currency", "fx_rate", "traded_at", "source", "evidence",
            "client_request_id", "note", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
        extra_kwargs = {
            # 允许只给数量单价，由 side 自动推导现金变动
            "amount": {"required": False, "allow_null": True},
            "quantity": {"required": False, "allow_null": True},
            "price": {"required": False, "allow_null": True},
            # 唯一性交给 create() 做幂等复用，不走 DRF 的唯一校验器（否则重复提交直接 400）
            "client_request_id": {"validators": []},
        }

    def validate(self, attrs):
        side = attrs.get("side")
        quantity = attrs.get("quantity") or Decimal("0")
        price = attrs.get("price") or Decimal("0")
        amount = attrs.get("amount")
        fee = attrs.get("fee") or Decimal("0")
        tax = attrs.get("tax") or Decimal("0")

        if side in ("BUY", "SELL") and not attrs.get("asset"):
            raise serializers.ValidationError({"asset": "买卖必须指定标的"})

        if amount is None:
            gross = quantity * price
            if side == "BUY":
                amount = -(gross + fee + tax)
            elif side == "SELL":
                amount = gross - fee - tax
            else:
                amount = gross - fee - tax
            attrs["amount"] = amount
        return attrs

    def create(self, validated_data):
        rid = validated_data.get("client_request_id")
        if rid:
            existed = Transaction.objects.filter(client_request_id=rid).first()
            if existed:
                return existed
        return super().create(validated_data)


class DividendRecordSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = DividendRecord
        fields = (
            "id", "user", "asset", "account", "transaction", "ex_date", "pay_date",
            "amount_per_share", "shares", "gross", "tax", "net", "currency",
            "reinvested", "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")
