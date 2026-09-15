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

    print()
    print(f"结果：{'-' if FAILS else '全部通过'}  {len(FAILS)} 个失败" if FAILS else "结果：全部通过")
    if FAILS:
        print("失败项：", ", ".join(FAILS))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()
