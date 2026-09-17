# -*- coding: utf-8 -*-
"""校验鸿蒙「记一笔」页使用的后端链路：登录 → 账户 → 标的自动建 → 买卖/股息/出入金。

跑法（**默认自启一个只服务当前代码的服务端**，跑完关掉）：

    python scripts/smoke_record_flow.py

要验一个已经在跑的服务（比如部署到服务器之后）：

    python scripts/smoke_record_flow.py --base http://127.0.0.1:8000

为什么不再写死 `127.0.0.1:8000`、以及 `--base` 模式下脚本为什么声明自己无法自证，
见 `_smoke_lib.py` 的说明 —— 那是被一次真实误诊换来的：写死地址时，脚本 26 条检查里
5 条 FAIL，全是端口上那个旧进程的旧字段造成的假缺陷，最后还 KeyError 中断。

本脚本会**真的写库**（和手工在鸿蒙端点一遍「记一笔」等价）。
"""
import json
import sys
import urllib.parse

import requests

from _smoke_lib import Report, run

USERNAME = "record_tester"
PASSWORD = "record_pwd_123"


def body(base: str, report: Report) -> None:
    check = report.check
    s = requests.Session()
    r = s.post(f"{base}/auth/register/", json={"username": USERNAME, "password": PASSWORD})
    if r.status_code >= 400:
        r = s.post(f"{base}/auth/token/", json={"username": USERNAME, "password": PASSWORD})
    check("注册/登录", r.status_code < 400, r.text[:200])
    r = s.post(f"{base}/auth/token/", json={"username": USERNAME, "password": PASSWORD})
    check("取 JWT", r.status_code < 400 and "access" in r.json(), r.text[:200])
    token = r.json()["access"]
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.post(f"{base}/accounts/", json={"name": "鸿蒙测试账户", "broker": "测试", "market": "A", "currency": "CNY"})
    if r.status_code == 400:
        r = s.get(f"{base}/accounts/")
        acct = r.json()["results"][0]
    else:
        acct = r.json()
    check("账户可用", "id" in acct, acct)
    aid = acct["id"]

    # 1) 标的不存在 → 自动创建；再次查询应命中同一条（复用，不重复建）
    sym = "601398"
    r = s.get(f"{base}/assets/?search={urllib.parse.quote(sym)}")
    check("标的搜索接口", r.status_code == 200, r.text[:200])
    found = [a for a in report.field(r.json(), "results", []) or [] if a["symbol"] == sym]
    if found:
        asset = found[0]
        check("标的复用（不重复建）", True)
    else:
        r = s.post(f"{base}/assets/", json={"symbol": sym, "name": "工商银行", "market": "A", "currency": "CNY", "is_dividend_asset": True})
        check("标的自动创建", r.status_code in (200, 201), r.text[:200])
        asset = r.json()

    # 2) 买入
    r = s.post(f"{base}/transactions/records/", json={
        "account": aid, "asset": asset["id"], "side": "BUY",
        "quantity": "1000", "price": "6.50", "fee": "5.00", "currency": "CNY",
        "traded_at": "2026-09-10T09:30:00", "client_request_id": "harmony-buy-1",
    })
    check("买入入账", r.status_code in (200, 201), r.text[:200])
    if r.status_code in (200, 201):
        check("买入现金变动为负", float(r.json()["amount"]) < 0, r.json().get("amount"))

    # 3) 幂等：同样 client_request_id 再提交一次
    r2 = s.post(f"{base}/transactions/records/", json={
        "account": aid, "asset": asset["id"], "side": "BUY",
        "quantity": "1000", "price": "6.50", "fee": "5.00", "currency": "CNY",
        "traded_at": "2026-09-10T09:30:00", "client_request_id": "harmony-buy-1",
    })
    same = r2.status_code in (200, 201) and r2.json().get("id") == r.json().get("id")
    check("重复提交幂等", same, r2.text[:200])

    # 4) 股息（手工填写）
    r = s.post(f"{base}/transactions/dividends/", json={
        "asset": asset["id"], "account": aid, "pay_date": "2026-09-12",
        "amount_per_share": "0.30", "shares": "1000",
        "gross": "300.00", "tax": "30.00", "net": "270.00", "currency": "CNY",
    })
    check("股息入账", r.status_code in (200, 201), r.text[:200])

    # 5) 入金 / 出金（无标的）
    for side, amount in (("DEPOSIT", "50000.00"), ("WITHDRAW", "-10000.00")):
        r = s.post(f"{base}/transactions/records/", json={
            "account": aid, "asset": None, "side": side, "amount": amount,
            "currency": "CNY", "traded_at": "2026-09-01T09:30:00",
            "client_request_id": f"harmony-{side.lower()}-1",
        })
        check(f"{side} 入账", r.status_code in (200, 201), r.text[:200])

    # 5.1) 出入金**不带金额**必须被明确拒绝。这条路径以前会静默记成 amount=0，
    #      而 0 元的入金在 XIRR 里等于这笔钱从未投入 —— 不报错，界面上也看不出来。
    r = s.post(f"{base}/transactions/records/", json={
        "account": aid, "asset": None, "side": "DEPOSIT",
        "currency": "CNY", "traded_at": "2026-09-01T09:30:00",
        "client_request_id": "harmony-deposit-noamount-1",
    })
    check("出入金缺金额被拒（而不是静默记成 0）", r.status_code == 400, r.text[:200])

    # 6) 持仓推导
    r = s.get(f"{base}/analytics/positions/")
    check("持仓推导", r.status_code == 200, r.text[:200])
    rows = report.field(r.json(), "results", []) or []
    hit = [p for p in rows if p.get("symbol") == sym]
    check("持仓含刚记的标的", len(hit) == 1, json.dumps(rows, ensure_ascii=False)[:200])
    if hit:
        check("持仓数量=1000", float(hit[0]["quantity"]) == 1000.0, hit[0].get("quantity"))

    # 7) 总览
    r = s.get(f"{base}/analytics/summary/")
    check("总览", r.status_code == 200, r.text[:200])
    summary = r.json() if r.status_code == 200 else {}

    # 8) 股息口径三处必须同源（持仓明细 / 总览 / 月度分布）
    #    修复前的实际故障：持仓页读「股息流水」、统计页读「股息明细」，同一个数字
    #    一个是 0 一个是 810。这里断言三者相等，任何一处再分家都会当场报出来。
    r = s.get(f"{base}/analytics/positions/")
    rows = report.field(r.json(), "results", []) or []
    pos_dividend = sum(float(p.get("dividend_total") or 0) for p in rows)
    total_dividend = float(report.field(summary, "dividend_total", 0) or 0)
    check(
        "持仓页股息合计 == 统计页累计股息",
        abs(pos_dividend - total_dividend) < 1e-9,
        f"{pos_dividend} vs {total_dividend}",
    )
    r = s.get(f"{base}/analytics/dividends/")
    months = report.field(r.json(), "months", []) if r.status_code == 200 else []
    check(
        "月度分布合计 == 统计页累计股息",
        abs(sum(float(m.get("net") or 0) for m in months) - total_dividend) < 1e-9,
        json.dumps(months, ensure_ascii=False)[:200],
    )

    # 9) 股息率与月度被动收入
    #    先判「字段在不在」，再算值。顺序很重要：字段不在时下面的算式只会得出一个
    #    没意义的差额，把「服务端是旧进程」伪装成「算错了」—— 这里踩过。
    has_yield = check("总览含股息率字段", "dividend_yield" in summary, sorted(summary))
    has_monthly = check("总览含月度被动收入字段", "monthly_passive_income" in summary, sorted(summary))
    if total_dividend > 0:
        cost = float(report.field(summary, "cost_basis", 0) or 0)
        # 只断言「近一年股息 > 0」而不是等于累计股息：脚本里的日期是写死的，
        # 一年之后这些股息会自然滑出观察窗口，断言相等会变成定时炸弹。
        annual = float(report.field(summary, "dividend_annual", 0) or 0)
        check("总览含近一年股息", annual > 0, report.field(summary, "dividend_annual"))
        if cost > 0 and has_yield and has_monthly:
            check(
                "股息率 == 近一年股息 / 成本",
                abs(float(report.field(summary, "dividend_yield", 0)) - annual / cost) < 1e-9,
                report.field(summary, "dividend_yield"),
            )
            check(
                "月度被动收入 == 近一年股息 / 12",
                abs(float(report.field(summary, "monthly_passive_income", 0)) - annual / 12) < 1e-9,
                report.field(summary, "monthly_passive_income"),
            )

    # 10) 形态 B：股息只落一条流水（没有股息明细）—— 这条路径曾经直接 500
    r = s.post(f"{base}/transactions/records/", json={
        "account": aid, "asset": asset["id"], "side": "DIVIDEND", "amount": "12.34",
        "currency": "CNY", "traded_at": "2026-07-01T14:20:00", "client_request_id": "harmony-dividend-tx-1",
    })
    check("形态B 股息流水入账", r.status_code in (200, 201), r.text[:200])
    r = s.get(f"{base}/analytics/summary/")
    check("形态B 下总览不报错", r.status_code == 200, f"HTTP {r.status_code} {r.text[:160]}")
    if r.status_code == 200:
        got = float(report.field(r.json(), "dividend_total", 0) or 0)
        check("形态B 的股息被算进累计股息", got >= 12.34, r.json().get("dividend_total"))


if __name__ == "__main__":
    sys.exit(run(body))
