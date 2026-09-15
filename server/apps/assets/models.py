from django.db import models

from apps.core.models import AssetType, Market, TimeStampedModel


class Asset(TimeStampedModel):
    """可交易标的（股票/ETF/基金/加密/外汇对）。"""

    symbol = models.CharField("代码", max_length=32)
    name = models.CharField("名称", max_length=128, blank=True)
    market = models.CharField("市场", max_length=16, choices=Market.choices, default=Market.A)
    asset_type = models.CharField("类型", max_length=16, choices=AssetType.choices, default=AssetType.STOCK)
    currency = models.CharField("计价货币", max_length=8, default="CNY")
    is_dividend_asset = models.BooleanField("股息资产", default=False)
    note = models.CharField("备注", max_length=255, blank=True)

    class Meta:
        verbose_name = "标的"
        verbose_name_plural = "标的"
        ordering = ("market", "symbol")
        unique_together = (("market", "symbol"),)
        indexes = (models.Index(fields=("market", "symbol")),)

    def __str__(self) -> str:
        return f"{self.symbol} {self.name}".strip()
