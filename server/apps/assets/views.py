from rest_framework import viewsets

from .models import Asset
from .serializers import AssetSerializer


class AssetViewSet(viewsets.ModelViewSet):
    """标的库，全局共享（不按用户隔离）。"""

    queryset = Asset.objects.all()
    serializer_class = AssetSerializer
    filterset_fields = ("market", "asset_type", "currency", "is_dividend_asset")
    search_fields = ("symbol", "name")
    ordering_fields = ("symbol", "name", "created_at")
