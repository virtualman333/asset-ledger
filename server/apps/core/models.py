"""通用抽象模型与枚举选择。"""
from django.db import models


class TimeStampedModel(models.Model):
    created_at = models.DateTimeField("创建时间", auto_now_add=True)
    updated_at = models.DateTimeField("更新时间", auto_now=True)

    class Meta:
        abstract = True


class Market(models.TextChoices):
    A = "A", "A股"
    HK = "HK", "港股"
    US = "US", "美股"
    FUND = "FUND", "基金"
    CRYPTO = "CRYPTO", "数字货币"
    FOREX = "FOREX", "外汇"
    OTHER = "OTHER", "其他"


class AssetType(models.TextChoices):
    STOCK = "STOCK", "股票"
    ETF = "ETF", "ETF"
    BOND = "BOND", "债券"
    FUND = "FUND", "基金"
    CRYPTO = "CRYPTO", "数字货币"
    FOREX = "FOREX", "外汇"
    CASH = "CASH", "现金"
    OTHER = "OTHER", "其他"


class TxSide(models.TextChoices):
    BUY = "BUY", "买入"
    SELL = "SELL", "卖出"
    DIVIDEND = "DIVIDEND", "分红/利息"
    DEPOSIT = "DEPOSIT", "入金"
    WITHDRAW = "WITHDRAW", "出金"
    FEE = "FEE", "费用"
    TAX = "TAX", "税费"
    SPLIT = "SPLIT", "拆分/送股"


class TxSource(models.TextChoices):
    MANUAL = "MANUAL", "手动"
    AGENT = "AGENT", "Agent 识别"
    IMPORT = "IMPORT", "导入"
