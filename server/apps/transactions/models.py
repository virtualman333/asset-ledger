from django.conf import settings
from django.db import models

from apps.core.models import TimeStampedModel, TxSide, TxSource


class Transaction(TimeStampedModel):
    """流水：全系统唯一事实源。

    amount 表示账户现金变动，正数=资金流入，负数=资金流出，已含 fee/tax。
    """

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="transactions", verbose_name="用户")
    account = models.ForeignKey("accounts.Account", on_delete=models.PROTECT, related_name="transactions", verbose_name="账户")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="transactions", null=True, blank=True, verbose_name="标的")
    side = models.CharField("方向", max_length=16, choices=TxSide.choices)
    quantity = models.DecimalField("数量", max_digits=24, decimal_places=8, null=True, blank=True)
    price = models.DecimalField("单价", max_digits=24, decimal_places=8, null=True, blank=True)
    amount = models.DecimalField("现金变动", max_digits=24, decimal_places=8)
    fee = models.DecimalField("手续费", max_digits=20, decimal_places=8, default=0)
    tax = models.DecimalField("税费", max_digits=20, decimal_places=8, default=0)
    currency = models.CharField("币种", max_length=8, default="CNY")
    fx_rate = models.DecimalField("入账汇率", max_digits=20, decimal_places=8, default=1)
    traded_at = models.DateTimeField("发生时间")
    source = models.CharField("来源", max_length=16, choices=TxSource.choices, default=TxSource.MANUAL)
    evidence = models.ForeignKey("ingest.Evidence", on_delete=models.SET_NULL, null=True, blank=True, related_name="transactions", verbose_name="原始凭证")
    client_request_id = models.CharField("幂等键", max_length=64, null=True, blank=True, unique=True, db_index=True)
    note = models.CharField("备注", max_length=255, blank=True)

    class Meta:
        verbose_name = "流水"
        verbose_name_plural = "流水"
        ordering = ("-traded_at", "-id")
        indexes = (
            models.Index(fields=("user", "-traded_at")),
            models.Index(fields=("account", "asset")),
        )

    def __str__(self) -> str:
        return f"{self.traded_at:%Y-%m-%d} {self.side} {self.asset} {self.amount}"


class DividendRecord(TimeStampedModel):
    """股息/利息明细，可独立存在（未关联流水则视为待入账记录）。"""

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="dividends", verbose_name="用户")
    asset = models.ForeignKey("assets.Asset", on_delete=models.PROTECT, related_name="dividends", verbose_name="标的")
    account = models.ForeignKey("accounts.Account", on_delete=models.SET_NULL, null=True, blank=True, related_name="dividends", verbose_name="账户")
    transaction = models.OneToOneField(Transaction, on_delete=models.SET_NULL, null=True, blank=True, related_name="dividend", verbose_name="关联流水")
    ex_date = models.DateField("除权日", null=True, blank=True)
    pay_date = models.DateField("派息日", null=True, blank=True)
    amount_per_share = models.DecimalField("每股派息", max_digits=20, decimal_places=8, null=True, blank=True)
    shares = models.DecimalField("持股数", max_digits=24, decimal_places=8, null=True, blank=True)
    gross = models.DecimalField("税前", max_digits=20, decimal_places=8, default=0)
    tax = models.DecimalField("税费", max_digits=20, decimal_places=8, default=0)
    net = models.DecimalField("税后", max_digits=20, decimal_places=8, default=0)
    currency = models.CharField("币种", max_length=8, default="CNY")
    reinvested = models.BooleanField("分红再投", default=False)

    class Meta:
        verbose_name = "股息记录"
        verbose_name_plural = "股息记录"
        ordering = ("-pay_date", "-id")
        indexes = (models.Index(fields=("user", "-pay_date")),)

    def __str__(self) -> str:
        return f"{self.asset} {self.pay_date} {self.net}"
