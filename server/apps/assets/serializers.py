from rest_framework import serializers

from .models import Asset


class AssetSerializer(serializers.ModelSerializer):
    class Meta:
        model = Asset
        fields = ("id", "symbol", "name", "market", "asset_type", "currency", "is_dividend_asset", "note", "created_at", "updated_at")
        read_only_fields = ("id", "created_at", "updated_at")
