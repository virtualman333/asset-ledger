from rest_framework import serializers

from .amount_rules import AmountError, resolve_amount
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

        if side in ("BUY", "SELL") and not attrs.get("asset"):
            raise serializers.ValidationError({"asset": "买卖必须指定标的"})

        # 现金变动的口径只有一处（`amount_rules.resolve_amount`）。这里不再自己推导 ——
        # 原先那条 `else` 分支把「入金 / 出金 / 拆分」和买入卖出算成同一件事，于是
        # 一笔入金的金额静默变成 0（详见该模块 docstring）。
        try:
            attrs["amount"] = resolve_amount(
                side,
                quantity=attrs.get("quantity"),
                price=attrs.get("price"),
                fee=attrs.get("fee"),
                tax=attrs.get("tax"),
                amount=attrs.get("amount"),
            )
        except AmountError as exc:
            # 「入金没填金额」是用户补一下就能解决的事，回 400 而不是 500
            raise serializers.ValidationError({"amount": str(exc)}) from exc
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
