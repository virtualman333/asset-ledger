from django.db import models

from apps.assets.models import Asset


class PriceQuote(models.Model):
    """行情快照。只增不改，便于回看走势。"""

    asset = models.ForeignKey(Asset, on_delete=models.CASCADE, related_name="quotes", verbose_name="标的")
    price = models.DecimalField("价格", max_digits=24, decimal_places=8)
    currency = models.CharField("币种", max_length=8, default="CNY")
    change_pct = models.DecimalField("涨跌幅%", max_digits=12, decimal_places=4, null=True, blank=True)
    source = models.CharField("数据源", max_length=32, default="")
    fetched_at = models.DateTimeField("抓取时间", auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "行情快照"
        verbose_name_plural = "行情快照"
        ordering = ("-fetched_at",)
        indexes = (models.Index(fields=("asset", "-fetched_at")),)

    def __str__(self) -> str:
        return f"{self.asset} {self.price} @{self.fetched_at:%Y-%m-%d %H:%M}"


class FxRate(models.Model):
    base = models.CharField("源币种", max_length=8)
    quote = models.CharField("目标币种", max_length=8)
    rate = models.DecimalField("汇率", max_digits=20, decimal_places=8)
    date = models.DateField("日期")
    source = models.CharField("数据源", max_length=32, default="")

    class Meta:
        verbose_name = "汇率"
        verbose_name_plural = "汇率"
        unique_together = (("base", "quote", "date"),)
        ordering = ("-date",)

    def __str__(self) -> str:
        return f"{self.base}/{self.quote} {self.rate}"
