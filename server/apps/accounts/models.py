from django.conf import settings
from django.db import models

from apps.core.models import Market, TimeStampedModel


class Account(TimeStampedModel):
    """券商/银行/钱包账户。"""

    class AccountType(models.TextChoices):
        BROKER = "BROKER", "券商"
        BANK = "BANK", "银行"
        CRYPTO = "CRYPTO", "交易所/钱包"
        CASH = "CASH", "现金"
        OTHER = "OTHER", "其他"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="accounts", verbose_name="用户")
    name = models.CharField("账户名称", max_length=64)
    broker = models.CharField("机构", max_length=64, blank=True)
    market = models.CharField("市场", max_length=16, choices=Market.choices, default=Market.A)
    account_type = models.CharField("类型", max_length=16, choices=AccountType.choices, default=AccountType.BROKER)
    currency = models.CharField("本位币", max_length=8, default="CNY")
    note = models.CharField("备注", max_length=255, blank=True)

    class Meta:
        verbose_name = "账户"
        verbose_name_plural = "账户"
        ordering = ("-created_at",)
        unique_together = (("user", "name"),)

    def __str__(self) -> str:
        return f"{self.name}({self.broker or '-'})"
