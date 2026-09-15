from rest_framework import serializers

from .models import Account


class AccountSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())

    class Meta:
        model = Account
        fields = ("id", "user", "name", "broker", "market", "account_type", "currency", "note", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
