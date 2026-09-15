"""一键造演示数据：账户 + 标的 + 流水 + 行情，用于验证账本引擎。

用法：python manage.py seed_demo
"""
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from apps.accounts.models import Account
from apps.analytics.services import build_positions, build_summary
from apps.assets.models import Asset
from apps.core.models import Market, TxSide
from apps.market.models import PriceQuote
from apps.market.services import fetch_quote
from apps.transactions.models import DividendRecord, Transaction

User = get_user_model()


class Command(BaseCommand):
    help = "生成演示数据（账户/标的/流水/行情）"

    def handle(self, *args, **options):
        user, _ = User.objects.get_or_create(username="demo", defaults={"email": "demo@example.com"})
        if not user.has_usable_password():
            user.set_password("demo12345")
            user.save()

        accounts = {
            "A": Account.objects.get_or_create(
                user=user, name="华泰证券", defaults={"broker": "华泰", "market": Market.A, "currency": "CNY"}
            )[0],
            "US": Account.objects.get_or_create(
                user=user, name="老虎证券", defaults={"broker": "Tiger", "market": Market.US, "currency": "USD"}
            )[0],
            "CRYPTO": Account.objects.get_or_create(
                user=user, name="OKX 现货", defaults={"broker": "OKX", "market": Market.CRYPTO, "currency": "USDT"}
            )[0],
        }

        assets = {
            "600519": Asset.objects.get_or_create(
                market=Market.A, symbol="600519", defaults={"name": "贵州茅台", "currency": "CNY", "is_dividend_asset": True}
            )[0],
            "AAPL": Asset.objects.get_or_create(
                market=Market.US, symbol="AAPL", defaults={"name": "Apple", "currency": "USD", "is_dividend_asset": True}
            )[0],
            "BTC": Asset.objects.get_or_create(
                market=Market.CRYPTO, symbol="BTC", defaults={"name": "Bitcoin", "currency": "USDT"}
            )[0],
        }

        if not Transaction.objects.filter(user=user).exists():
            now = timezone.now()
            rows = [
                dict(account=accounts["A"], asset=None, side=TxSide.DEPOSIT, amount=Decimal("100000"), currency="CNY"),
                dict(account=accounts["A"], asset=assets["600519"], side=TxSide.BUY, quantity=Decimal("100"), price=Decimal("1680"), fee=Decimal("5"), amount=Decimal("-168005"), currency="CNY"),
                dict(account=accounts["US"], asset=assets["AAPL"], side=TxSide.BUY, quantity=Decimal("10"), price=Decimal("180"), fee=Decimal("1"), amount=Decimal("-1801"), currency="USD"),
                dict(account=accounts["CRYPTO"], asset=assets["BTC"], side=TxSide.BUY, quantity=Decimal("0.5"), price=Decimal("60000"), fee=Decimal("6"), amount=Decimal("-30006"), currency="USDT"),
            ]
            for row in rows:
                tx = Transaction(user=user, traded_at=now, source="MANUAL", **row)
                tx.full_clean(exclude=("client_request_id",))
                tx.save()

            dividend_tx = Transaction(
                user=user,
                account=accounts["A"],
                asset=assets["600519"],
                side=TxSide.DIVIDEND,
                quantity=Decimal("100"),
                price=Decimal("8"),
                amount=Decimal("800"),
                currency="CNY",
                traded_at=now,
            )
            dividend_tx.save()
            DividendRecord.objects.get_or_create(
                user=user,
                asset=assets["600519"],
                account=accounts["A"],
                transaction=dividend_tx,
                defaults={"pay_date": now.date(), "amount_per_share": Decimal("8"), "shares": Decimal("100"), "gross": Decimal("800"), "net": Decimal("800"), "currency": "CNY"},
            )
            self.stdout.write(self.style.SUCCESS("已写入演示流水"))

        for asset in assets.values():
            data = fetch_quote(asset)
            if data:
                PriceQuote.objects.create(
                    asset=asset,
                    price=data["price"],
                    currency=data.get("currency") or asset.currency,
                    change_pct=data.get("change_pct"),
                    source=data.get("source", ""),
                )
                self.stdout.write(f"行情 {asset.symbol}: {data['price']} {data.get('currency')} ({data.get('source')})")
            else:
                self.stdout.write(self.style.WARNING(f"行情抓取失败：{asset.symbol}（外网不可达时会走缓存兜底）"))

        self.stdout.write("\n=== 持仓 ===")
        for pos in build_positions(user):
            self.stdout.write(
                f"{pos['symbol']:>8} 数量={pos['quantity']:>12} 均价={pos['avg_cost']:>12} "
                f"现价={pos['last_price']} 浮盈={pos['unrealized_pnl']}"
            )
        self.stdout.write("\n=== 总览 ===")
        self.stdout.write(str(build_summary(user)))
        self.stdout.write(self.style.SUCCESS("\n完成。登录账号：demo / demo12345"))
