"""流水金额的口径：按 side 推导现金变动、把符号归一到模型的约定上。**唯一定义处。**

`Transaction.amount` 的约定写在模型 docstring 上：**正数 = 资金流入，负数 = 资金流出**。
但这条约定此前没有任何地方执行，它只活在每个调用方的记忆里，于是：

1. **入金 / 出金静默变成 0。** 两者没有数量单价可推，却和买入卖出走了同一条
   `else` 分支（`amount = 数量×单价 - fee - tax`）。Agent 识别「入金 100000」的截图时
   正好落在这条路径上（草稿 schema 里没有 `amount` 字段），于是流水金额是 `0` ——
   而 `build_summary` 的 XIRR 正是靠出入金定现金流的：金额为 0 的入金等于这笔钱
   从未投入，年化算出来的数字与实际情况无关，**全程不报任何错**。
2. **拆分/送股会凭空多出一笔现金。** 拆股本身不产生现金变动，但它同样走了那条
   分支 —— 录入时只要顺带填了单价，`amount` 就是一个正数，总资产随之虚增。
3. **出入金的符号看运气。** 用户按直觉把「出金 50000」填成正数，与模型约定相反；
   而 XIRR 用 `-amount` 定方向，出金于是被当成入金。

规则放在这里而不是 serializer 里，理由与 `apps/analytics/dividend_income.py` 相同：
serializer 需要 Django，而这层判断不需要 —— 纯 Python 才能被直接单测覆盖。
（`apps/transactions/tests/test_amount_rules.py`：不需要数据库、不需要 Django settings。）
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation

ZERO = Decimal("0")

#: 现金方向完全由 side 决定的两档 —— 金额给正给负都按 side 归一。
#: 其它档（买入/卖出/分红/费用/税费）的符号有细微语义（比如红利税走 DIVIDEND
#: 的负数冲销），显式传入时原样尊重，不擅自翻转。
SIGN_BY_SIDE = {"DEPOSIT": 1, "WITHDRAW": -1}

#: 必须显式给金额、推不出来的档：「入金」只有金额，没有数量×单价。
AMOUNT_REQUIRED = ("DEPOSIT", "WITHDRAW")


class AmountError(ValueError):
    """金额无法确定，或与方向矛盾到无法归一。

    调用方应把它转成 400（用户能改）而不是 500（平台故障）——
    「入金没填金额」是用户补一下就能解决的事。
    """


def _dec(value) -> Decimal:
    """转 Decimal：None / 空串按 0，非法值报 AmountError 而不是抛 InvalidOperation。

    非法值走 `AmountError` 是为了让调用方只需要 catch 一个异常类型；
    直接漏出 `decimal.InvalidOperation` 会被 DRF 当成 500。
    """
    if value in (None, ""):
        return ZERO
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise AmountError(f"金额不是有效数字：{value!r}") from exc


def resolve_amount(side, *, quantity=None, price=None, fee=None, tax=None, amount=None) -> Decimal:
    """定下这条流水的现金变动。**全系统唯一实现。**

    - 给了 `amount` 就用它；出入金按 side 归一符号（正数 = 流入）。
    - 没给就按 `数量×单价` 推，再减费用税费。
    - 推不出来的档（出入金）**直接报错，绝不返回 0** —— 猜一个 0 出来就是上面
      第 1 条那个缺陷：一笔入金从年化里静默消失，比报错难查得多。
    """
    side = (side or "").upper()
    fee_d, tax_d = _dec(fee), _dec(tax)

    if amount is not None:
        value = _dec(amount)
        if side in SIGN_BY_SIDE:
            if value == ZERO:
                raise AmountError("入金 / 出金的金额不能为 0 —— 这笔钱在年化里等于没发生过")
            return abs(value) * SIGN_BY_SIDE[side]
        return value

    if side in AMOUNT_REQUIRED:
        raise AmountError(
            "入金 / 出金必须填写金额 —— 它没有数量×单价可推，"
            "猜出来的 0 会让这笔现金流在年化里凭空消失"
        )

    if side == "SPLIT":
        # 拆分 / 送股不产生现金变动。此前它与买入卖出共用一条分支，
        # 录入时只要带上了单价就会凭空多出一笔现金流入。
        return ZERO

    gross = _dec(quantity) * _dec(price)
    if side == "BUY":
        return -(gross + fee_d + tax_d)
    # SELL / DIVIDEND / FEE / TAX：现金进账，再减掉费用与税费
    return gross - fee_d - tax_d
