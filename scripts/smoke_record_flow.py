"""校验鸿蒙「记一笔」页使用的后端链路：登录 → 账户 → 标的自动建 → 买卖/股息/出入金。"""
import json
import sys
import urllib.parse

import requests

BASE = "http://127.0.0.1:8000/api/v1"
USERNAME = "record_tester"
PASSWORD = "record_pwd_123"

FAILS = []


def check(name, ok, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {name}{'' if ok else '  -> ' + str(detail)[:200]}")
    if not ok:
        FAILS.append(name)


def main():
    s = requests.Session()
    r = s.post(f"{BASE}/auth/register/", json={"username": USERNAME, "password": PASSWORD})
    if r.status_code >= 400:
        r = s.post(f"{BASE}/auth/token/", json={"username": USERNAME, "password": PASSWORD})
    check("注册/登录", r.status_code < 400, r.text[:200])
    r = s.post(f"{BASE}/auth/token/", json={"username": USERNAME, "password": PASSWORD})
    check("取 JWT", r.status_code < 400 and "access" in r.json(), r.text[:200])
    token = r.json()["access"]
    s.headers["Authorization"] = f"Bearer {token}"

    r = s.post(f"{BASE}/accounts/", json={"name": "鸿蒙测试账户", "broker": "测试", "market": "A", "currency": "CNY"})
    if r.status_code == 400:
        r = s.get(f"{BASE}/accounts/")
        acct = r.json()["results"][0]
    else:
        acct = r.json()
    check("账户可用", "id" in acct, acct)
    aid = acct["id"]

    # 1) 标的不存在 → 自动创建；再次查询应命中同一条（复用，不重复建）
    sym = "601398"
    r = s.get(f"{BASE}/assets/?search={urllib.parse.quote(sym)}")
    check("标的搜索接口", r.status_code == 200, r.text[:200])
    found = [a for a in r.json().get("results", []) if a["symbol"] == sym]
    if found:
        asset = found[0]
        check("标的复用（不重复建）", True)
    else:
        r = s.post(f"{BASE}/assets/", json={"symbol": sym, "name": "工商银行", "market": "A", "currency": "CNY", "is_dividend_asset": True})
        check("标的自动创建", r.status_code in (200, 201), r.text[:200])
        asset = r.json()

    # 2) 买入
    r = s.post(f"{BASE}/transactions/records/", json={
        "account": aid, "asset": asset["id"], "side": "BUY",
        "quantity": "1000", "price": "6.50", "fee": "5.00", "currency": "CNY",
        "traded_at": "2026-09-10T09:30:00", "client_request_id": "harmony-buy-1",
    })
    check("买入入账", r.status_code in (200, 201), r.text[:200])
    if r.status_code in (200, 201):
        check("买入现金变动为负", float(r.json()["amount"]) < 0, r.json().get("amount"))

    # 3) 幂等：同样 client_request_id 再提交一次
    r2 = s.post(f"{BASE}/transactions/records/", json={
        "account": aid, "asset": asset["id"], "side": "BUY",
        "quantity": "1000", "price": "6.50", "fee": "5.00", "currency": "CNY",
        "traded_at": "2026-09-10T09:30:00", "client_request_id": "harmony-buy-1",
    })
    same = r2.status_code in (200, 201) and r2.json().get("id") == r.json().get("id")
    check("重复提交幂等", same, r2.text[:200])

    # 4) 股息（手工填写）
    r = s.post(f"{BASE}/transactions/dividends/", json={
        "asset": asset["id"], "account": aid, "pay_date": "2026-09-12",
        "amount_per_share": "0.30", "shares": "1000",
        "gross": "300.00", "tax": "30.00", "net": "270.00", "currency": "CNY",
    })
    check("股息入账", r.status_code in (200, 201), r.text[:200])

    # 5) 入金 / 出金（无标的）
    for side, amount in (("DEPOSIT", "50000.00"), ("WITHDRAW", "-10000.00")):
        r = s.post(f"{BASE}/transactions/records/", json={
            "account": aid, "asset": None, "side": side, "amount": amount,
            "currency": "CNY", "traded_at": "2026-09-01T09:30:00",
            "client_request_id": f"harmony-{side.lower()}-1",
        })
        check(f"{side} 入账", r.status_code in (200, 201), r.text[:200])

    # 5.1) 出入金**不带金额**必须被明确拒绝。这条路径以前会静默记成 amount=0，
    #      而 0 元的入金在 XIRR 里等于这笔钱从未投入 —— 不报错，界面上也看不出来。
    r = s.post(f"{BASE}/transactions/records/", json={
        "account": aid, "asset": None, "side": "DEPOSIT",
        "currency": "CNY", "traded_at": "2026-09-01T09:30:00",
        "client_request_id": "harmony-deposit-noamount-1",
    })
    check("出入金缺金额被拒（而不是静默记成 0）", r.status_code == 400, r.text[:200])

    # 6) 持仓推导
    r = s.get(f"{BASE}/analytics/positions/")
    check("持仓推导", r.status_code == 200, r.text[:200])
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    hit = [p for p in rows if p.get("symbol") == sym]
    check("持仓含刚记的标的", len(hit) == 1, json.dumps(rows, ensure_ascii=False)[:200])
    if hit:
        check("持仓数量=1000", float(hit[0]["quantity"]) == 1000.0, hit[0].get("quantity"))

    # 7) 总览
    r = s.get(f"{BASE}/analytics/summary/")
    check("总览", r.status_code == 200, r.text[:200])
    summary = r.json() if r.status_code == 200 else {}

    # 8) 股息口径三处必须同源（持仓明细 / 总览 / 月度分布）
    #    修复前的实际故障：持仓页读「股息流水」、统计页读「股息明细」，同一个数字
    #    一个是 0 一个是 810。这里断言三者相等，任何一处再分家都会当场报出来。
    r = s.get(f"{BASE}/analytics/positions/")
    rows = r.json() if isinstance(r.json(), list) else r.json().get("results", [])
    pos_dividend = sum(float(p.get("dividend_total") or 0) for p in rows)
    total_dividend = float(summary.get("dividend_total") or 0)
    check(
        "持仓页股息合计 == 统计页累计股息",
        abs(pos_dividend - total_dividend) < 1e-9,
        f"{pos_dividend} vs {total_dividend}",
    )
    r = s.get(f"{BASE}/analytics/dividends/")
    months = r.json().get("months", []) if r.status_code == 200 else []
    check(
        "月度分布合计 == 统计页累计股息",
        abs(sum(float(m.get("net") or 0) for m in months) - total_dividend) < 1e-9,
        json.dumps(months, ensure_ascii=False)[:200],
    )

    # 9) 股息率与月度被动收入
    check("总览含股息率字段", "dividend_yield" in summary, sorted(summary))
    check("总览含月度被动收入字段", "monthly_passive_income" in summary, sorted(summary))
    if total_dividend > 0:
        cost = float(summary.get("cost_basis") or 0)
        # 只断言「近一年股息 > 0」而不是等于累计股息：脚本里的日期是写死的，
        # 一年之后这些股息会自然滑出观察窗口，断言相等会变成定时炸弹。
        check("总览含近一年股息", float(summary.get("dividend_annual") or 0) > 0,
              summary.get("dividend_annual"))
        if cost > 0:
            check(
                "股息率 == 近一年股息 / 成本",
                abs(float(summary["dividend_yield"]) - float(summary["dividend_annual"]) / cost) < 1e-9,
                summary.get("dividend_yield"),
            )
            check(
                "月度被动收入 == 近一年股息 / 12",
                abs(float(summary["monthly_passive_income"]) - float(summary["dividend_annual"]) / 12) < 1e-9,
                summary.get("monthly_passive_income"),
            )

    # 10) 形态 B：股息只落一条流水（没有股息明细）—— 这条路径曾经直接 500
    r = s.post(f"{BASE}/transactions/records/", json={
        "account": aid, "asset": asset["id"], "side": "DIVIDEND", "amount": "12.34",
        "currency": "CNY", "traded_at": "2026-07-01T14:20:00", "client_request_id": "harmony-dividend-tx-1",
    })
    check("形态B 股息流水入账", r.status_code in (200, 201), r.text[:200])
    r = s.get(f"{BASE}/analytics/summary/")
    check("形态B 下总览不报错", r.status_code == 200, f"HTTP {r.status_code} {r.text[:160]}")
    if r.status_code == 200:
        check("形态B 的股息被算进累计股息",
              float(r.json()["dividend_total"]) >= 12.34, r.json().get("dividend_total"))

    print()
    print(f"结果：{'-' if FAILS else '全部通过'}  {len(FAILS)} 个失败" if FAILS else "结果：全部通过")
    if FAILS:
        print("失败项：", ", ".join(FAILS))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
