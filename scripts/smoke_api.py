# -*- coding: utf-8 -*-
"""API 冒烟测试：跑通注册 → 记账 → 行情 → 统计 → Agent 文本识别全链路。

跑法（**默认自启一个只服务当前代码的服务端**，跑完关掉）：

    python scripts/smoke_api.py

要验一个已经在跑的服务：

    python scripts/smoke_api.py --base http://127.0.0.1:8000

地址为什么不再写死、以及 `--base` 模式下为什么脚本会声明自己无法自证，见
`_smoke_lib.py` 的说明。本脚本会**真的写库**。
"""
import json
import sys
import time

import requests

from _smoke_lib import Report, run

USERNAME = "smoke_user"
PASSWORD = "smoke12345"


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def body(base: str, report: Report) -> None:
    check = report.check
    session = requests.Session()

    # 1. 注册
    r = session.post(f"{base}/auth/register/", json={"username": USERNAME, "email": "smoke@example.com", "password": PASSWORD})
    if r.status_code == 400 and "username" in r.text:
        report.skip("注册", "用户已存在，直接登录")
    elif r.status_code >= 400:
        check("注册", False, f"{r.status_code} {r.text[:200]}")
        return

    r = session.post(f"{base}/auth/token/", json={"username": USERNAME, "password": PASSWORD})
    check("登录获取 JWT", r.status_code == 200, r.text[:120])
    if r.status_code != 200:
        return
    token = r.json()["access"]
    headers = auth(token)

    # 2. 账户
    r = session.get(f"{base}/accounts/?search=冒烟账户", headers=headers)
    existed = report.field(r.json(), "results", []) or []
    if existed:
        account = existed[0]
    else:
        r = session.post(f"{base}/accounts/", json={"name": "冒烟账户", "broker": "测试券商", "market": "A", "currency": "CNY"}, headers=headers)
        account = r.json()
    check("创建账户", bool(account.get("id")), json.dumps(account, ensure_ascii=False)[:120])
    account_id = account["id"]

    # 3. 标的
    r = session.post(f"{base}/assets/", json={"symbol": "601398", "name": "工商银行", "market": "A", "currency": "CNY", "is_dividend_asset": True}, headers=headers)
    if r.status_code == 400:
        r = session.get(f"{base}/assets/?search=601398", headers=headers)
        rows = report.field(r.json(), "results", []) or []
        check("标的已存在时搜得到", bool(rows), r.text[:160])
        asset = rows[0] if rows else {}
    else:
        asset = r.json()
    check("创建标的", bool(asset.get("id")), f"{asset.get('symbol')} id={asset.get('id')}")
    asset_id = asset["id"]

    # 4. 记一笔买入（幂等键）
    rid = "smoke-buy-0001"
    payload = {"account": account_id, "asset": asset_id, "side": "BUY", "quantity": "1000", "price": "6.50", "fee": "5", "currency": "CNY", "traded_at": "2026-01-10T10:00:00+08:00", "client_request_id": rid}
    r = session.post(f"{base}/transactions/records/", json=payload, headers=headers)
    check("记录买入流水", r.status_code in (200, 201), r.text[:160])
    first_id = r.json().get("id") if r.status_code in (200, 201) else None

    r2 = session.post(f"{base}/transactions/records/", json=payload, headers=headers)
    check("幂等：重复提交不产生新流水", r2.status_code in (200, 201) and r2.json().get("id") == first_id, f"{first_id} vs {r2.json().get('id')}")

    # 5. 股息
    r = session.post(
        f"{base}/transactions/dividends/",
        json={"asset": asset_id, "account": account_id, "pay_date": "2026-07-15", "amount_per_share": "0.30", "shares": "1000", "gross": "300", "tax": "0", "net": "300", "currency": "CNY"},
        headers=headers,
    )
    check("记录股息", r.status_code in (200, 201), r.text[:160])

    # 6. 行情
    r = session.get(f"{base}/market/quotes/", params={"asset_ids": asset_id, "refresh": "1"}, headers=headers)
    quotes = report.field(r.json(), "results", []) or []
    check("抓取实时行情", r.status_code == 200 and bool(quotes) and quotes[0].get("price"), r.text[:160])

    r = session.get(f"{base}/market/fx/", params={"base": "USD", "quote": "CNY"}, headers=headers)
    check("获取汇率", r.status_code == 200 and report.field(r.json(), "rate"), r.text[:120])

    # 7. 统计
    r = session.get(f"{base}/analytics/positions/", headers=headers)
    check("持仓计算", r.status_code == 200 and len(report.field(r.json(), "results", []) or []) > 0, r.text[:160])
    r = session.get(f"{base}/analytics/summary/", headers=headers)
    check("总览统计", r.status_code == 200, json.dumps(r.json(), ensure_ascii=False)[:200])
    r = session.get(f"{base}/analytics/dividends/", params={"year": 2026}, headers=headers)
    check("股息月度聚合", r.status_code == 200 and len(report.field(r.json(), "months", []) or []) > 0, r.text[:160])

    # 8. Agent 文本识别（无 LLM Key 时走规则兜底）
    r = session.post(
        f"{base}/ingest/text/",
        json={"text": "证券买入 601398 工商银行 1000股 成交价 6.50元", "client_request_id": f"smoke-text-{int(time.time())}"},
        headers=headers,
    )
    check("Agent 文本抽取", r.status_code == 201, r.text[:200])
    job = r.json() if isinstance(r.json(), dict) else {}
    r = session.get(f"{base}/ingest/drafts/", headers=headers)
    check("草稿箱可见", r.status_code == 200, f"草稿数={len(report.field(r.json(), 'results', []) or [])}")

    if job.get("status") == "SUCCEEDED":
        r = session.post(
            f"{base}/ingest/drafts/{job['id']}/confirm/",
            json={"account_id": account_id, "overrides": {"traded_at": "2026-03-01T10:00:00+08:00"}},
            headers=headers,
        )
        check("草稿确认入账", r.status_code == 201, r.text[:200])
    else:
        report.skip("草稿确认入账", "草稿未识别成功（无 LLM Key 时属预期）")


if __name__ == "__main__":
    sys.exit(run(body))
