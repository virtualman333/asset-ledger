# -*- coding: utf-8 -*-
"""API 冒烟测试：跑通注册 → 记账 → 行情 → 统计 → Agent 文本识别 → 导出 CSV 全链路。

跑法（**默认自启一个只服务当前代码的服务端**，跑完关掉）：

    python scripts/smoke_api.py

要验一个已经在跑的服务（脚本会先向它要 `/health/`，拿源码指纹比对「跑的是不是这份代码」）：

    python scripts/smoke_api.py --base http://127.0.0.1:8000

地址为什么不再写死、`--base` 模式为什么要先做那次指纹核对（对不上就是一条 FAIL，并明说
后面的失败不能当缺陷读），见 `_smoke_lib.py` 的说明。本脚本会**真的写库**。
"""
import json
import sys
import time
from decimal import Decimal
from pathlib import Path

import requests

from _smoke_lib import Report, run

#: 列定义直接从服务端那两份纯模块读（`export_rules` / `dividend_export` 都不 import Django）。
#: **不在脚本里抄第二份列清单** —— 抄一份就等于把「列有没有变」这件事交给运气。
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
from apps.analytics.dividend_export import DIVIDEND_CSV_COLUMNS  # noqa: E402
from apps.transactions.export_rules import CSV_COLUMNS  # noqa: E402

USERNAME = "smoke_user"
PASSWORD = "smoke12345"

#: 导出用例用的日期：远期，避免与别的用例抢数据；入金不需要标的与数量单价
EXPORT_FROM = EXPORT_TO = "2027-01-01"
EXPORT_ROW = "2027-01-01 10:00:00,入金,,,冒烟账户,,,4321.5,0,0,CNY,1,手动,'=1+1"


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

    # 9. 导出 CSV —— 真发一次请求，看整条线（路由 → 复用列表的筛选 → 渲染 → 响应头）
    #    备注刻意写成公式：Excel 打开时会被当公式执行的那一类文本。
    r = session.post(
        f"{base}/transactions/records/",
        json={
            "account": account_id, "side": "DEPOSIT", "amount": "4321.50", "currency": "CNY",
            "traded_at": f"{EXPORT_FROM}T10:00:00+08:00", "client_request_id": "smoke-export-0001",
            "note": "=1+1",
        },
        headers=headers,
    )
    check("导出前的入金流水（备注故意写成公式）", r.status_code in (200, 201), r.text[:160])

    r = session.get(
        f"{base}/transactions/records/export/",
        params={"from": EXPORT_FROM, "to": EXPORT_TO}, headers=headers,
    )
    # 这一条同时钉住「路由没被 records/{pk}/ 吃掉」：排错位置的话 `export` 会被当成主键，是 500
    check("导出 CSV 返回 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    check("Content-Type 是 text/csv", r.headers.get("Content-Type", "").startswith("text/csv"),
          str(r.headers.get("Content-Type")))
    disposition = r.headers.get("Content-Disposition") or ""
    check("Content-Disposition 带中文名（响应头没因为非 latin-1 炸掉）",
          "filename*=UTF-8''" in disposition, disposition)

    text = r.text
    check("CSV 以 UTF-8 BOM 开头（否则 Excel 拿 GBK 解，中文列头变乱码）",
          text.startswith("\ufeff"), repr(text[:30]))

    lines = [ln for ln in text.split("\r\n") if ln != ""]
    check("列头与服务端 export_rules.CSV_COLUMNS 逐字一致",
          bool(lines) and lines[0].lstrip("\ufeff") == ",".join(t for _, t in CSV_COLUMNS),
          repr(lines[:1]))
    check("按日期筛出来恰好 1 条 —— 导出复用了列表的筛选，不是另起一套查询",
          len(lines) == 2, f"数据行 {max(len(lines) - 1, 0)} 条：{text[:200]}")
    check(
        "整行内容正确（时间按 Asia/Shanghai 落地、金额十进制、备注里的 =1+1 被中和）",
        len(lines) == 2 and lines[1] == EXPORT_ROW,
        f"实际：{lines[1] if len(lines) > 1 else '(没有数据行)'}",
    )

    # 筛选三件套：filter / search 各来一次，都要在导出上生效
    r = session.get(f"{base}/transactions/records/export/", params={"side": "SPLIT"}, headers=headers)
    split_lines = [ln for ln in r.text.split("\r\n") if ln != ""]
    check("导出认 side 筛选（SPLIT 一条都没有 → 只剩列头）",
          r.status_code == 200 and len(split_lines) == 1,
          f"{r.status_code} 行数={len(split_lines)}：{r.text[:160]}")

    r = session.get(f"{base}/transactions/records/export/", params={"search": "601398"}, headers=headers)
    hit_lines = [ln for ln in r.text.split("\r\n") if ln != ""][1:]
    check("导出认 search 筛选（命中行都含 601398）",
          r.status_code == 200 and bool(hit_lines) and all("601398" in ln for ln in hit_lines),
          f"{r.status_code}：{hit_lines[:2]}")

    # 10. 日期区间筛选在**列表**上也要真的生效。
    #     这一段原先用 `traded_at__date`，在没装时区表的 MySQL 上恒返回 0 条且不报错 ——
    #     所以这里不能只测导出，得连列表一起测：两处共用同一个 get_queryset()。
    r = session.get(
        f"{base}/transactions/records/",
        params={"from": EXPORT_FROM, "to": EXPORT_TO}, headers=headers,
    )
    rows = report.field(r.json(), "results", []) or []
    check("列表按日期区间能查到那笔入金（不是恒空）",
          r.status_code == 200 and any(
              row.get("side") == "DEPOSIT" and str(row.get("amount", "")).startswith("4321.5")
              for row in rows
          ),
          f"{r.status_code} 命中 {len(rows)} 条：{json.dumps(rows[:1], ensure_ascii=False)[:160]}")

    r = session.get(f"{base}/transactions/records/", params={"from": "abc"}, headers=headers)
    check("日期格式写错回 400（以前是 500）", r.status_code == 400, f"{r.status_code} {r.text[:160]}")

    r = session.get(
        f"{base}/transactions/records/export/",
        params={"from": "2027-01-02", "to": "2027-01-01"}, headers=headers,
    )
    check("to 早于 from 回 400（导出与列表同一套判据）",
          r.status_code == 400, f"{r.status_code} {r.text[:160]}")

    # 11. 股息导出 —— 这一整段是为了钉住一件**单元测试压不到**的事：
    #     股息有两条合法录入路径，只有一条会落 DividendRecord。导出若照「把明细表列出来」
    #     的写法实现，另一条会整条消失，而页面上「累计股息」是两条都算的。
    #     所以要真的走一遍 ORM → 归集 → 渲染，并且和页面上的数字对账。
    flow_dividend = {
        "account": account_id, "asset": asset_id, "side": "DIVIDEND", "amount": "77.25",
        "currency": "CNY", "traded_at": "2026-08-20T10:00:00+08:00",
        "client_request_id": "smoke-dividend-flow-0001", "note": "只落流水、没有股息明细",
    }
    r = session.post(f"{base}/transactions/records/", json=flow_dividend, headers=headers)
    check("记一笔只有流水的股息（没有 DividendRecord）",
          r.status_code in (200, 201), r.text[:160])

    r = session.get(f"{base}/analytics/dividends/export/", headers=headers)
    check("股息导出返回 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    check("股息导出 Content-Type 是 text/csv",
          r.headers.get("Content-Type", "").startswith("text/csv"),
          str(r.headers.get("Content-Type")))
    disposition = r.headers.get("Content-Disposition") or ""
    # 写死 URL 编码后的「股息」：引用常量算出来的名字就变成自证了
    check("股息导出的中文名里是「股息」（响应头没因为非 latin-1 炸掉）",
          "filename*=UTF-8''asset-ledger-%E8%82%A1%E6%81%AF-" in disposition, disposition)
    check("股息导出也带 UTF-8 BOM", r.text.startswith("\ufeff"), repr(r.text[:30]))

    d_lines = [ln for ln in r.text.split("\r\n") if ln != ""]
    check("股息导出的列头与 DIVIDEND_CSV_COLUMNS 逐字一致",
          bool(d_lines) and d_lines[0].lstrip("\ufeff") == ",".join(t for _, t in DIVIDEND_CSV_COLUMNS),
          repr(d_lines[:1]))

    d_titles = [t for _, t in DIVIDEND_CSV_COLUMNS]
    d_rows = [dict(zip(d_titles, ln.split(","))) for ln in d_lines[1:]]
    by_origin = {row["来源"]: row for row in d_rows}

    check("★ 只有流水的那笔股息也出现在文件里（照明细表导出会整条漏掉）",
          "流水录入" in by_origin,
          f"文件里的来源有：{sorted(by_origin)}")

    detail = by_origin.get("股息明细") or {}
    check("有明细那条：税前/税费/持股数/每股派息都填上了",
          detail.get("税前") == "300" and detail.get("税费") == "0"
          and detail.get("持股数") == "1000" and detail.get("每股派息") == "0.3",
          json.dumps(detail, ensure_ascii=False)[:200])
    check("有明细那条：税后 = net = 300，来源是「股息明细」",
          detail.get("税后") == "300" and detail.get("派息日") == "2026-07-15"
          and detail.get("标的代码") == "601398" and detail.get("账户") == "冒烟账户",
          json.dumps(detail, ensure_ascii=False)[:200])

    flow = by_origin.get("流水录入") or {}
    # 关键口径：写 0 等于替用户宣布「这笔没收过税」，必须留空格子
    check("★ 纯流水那条：明细那几列是**空格子**，不是 0",
          flow.get("税前") == "" and flow.get("税费") == "" and flow.get("持股数") == ""
          and flow.get("每股派息") == "" and flow.get("分红再投") == "",
          json.dumps(flow, ensure_ascii=False)[:200])
    check("纯流水那条：税后 = 流水金额绝对值 = 77.25，派息日 = 流水日期",
          flow.get("税后") == "77.25" and flow.get("派息日") == "2026-08-20",
          json.dumps(flow, ensure_ascii=False)[:200])

    # 与页面上的数字对账 —— 这是整段里最有价值的一条：把「导出与累计股息同口径」
    # 从一句话变成可执行的检查。两边都按「全部年份」比，所以不传 year。
    r = session.get(f"{base}/analytics/summary/", headers=headers)
    page_total = report.field(r.json(), "dividend_total")
    exported_total = sum(
        (Decimal(row["税后"]) for row in d_rows if row["税后"]), Decimal("0")
    )
    check("★ 导出里「税后」列的合计 == 总览的累计股息（同一口径，不是各算一遍）",
          page_total is not None and Decimal(str(page_total)) == exported_total,
          f"页面 dividend_total={page_total!r}，导出合计={exported_total}")

    # year 筛选与 /analytics/dividends/ 同口径
    r = session.get(f"{base}/analytics/dividends/export/", params={"year": "2026"}, headers=headers)
    y_lines = [ln for ln in r.text.split("\r\n") if ln != ""]
    check("股息导出认 year 筛选（2026 那两笔都在）",
          r.status_code == 200 and len(y_lines) - 1 == len(d_rows),
          f"{r.status_code} 行数={len(y_lines) - 1}（全部年份 {len(d_rows)}）")

    # 再问一个一年都没有的年份 —— 「2026 那两笔都在」锁不住「year 被整个忽略」：
    # 冒烟数据全在 2026，忽略筛选它照样绿。这类「不会响的检查」比没有检查更坏。
    r = session.get(f"{base}/analytics/dividends/export/", params={"year": "2020"}, headers=headers)
    empty_lines = [ln for ln in r.text.split("\r\n") if ln != ""]
    check("股息导出认 year 筛选（2020 一年都没有，只剩列头）",
          r.status_code == 200 and len(empty_lines) == 1,
          f"{r.status_code} 行数={len(empty_lines)}")

    r = session.get(f"{base}/analytics/dividends/export/", params={"year": "abc"}, headers=headers)
    check("股息导出的 year 写错回 400（以前是 500）", r.status_code == 400, f"{r.status_code} {r.text[:160]}")

    r = session.get(f"{base}/analytics/dividends/", params={"year": "abc"}, headers=headers)
    check("同一个 year 判据也管着图表接口（解析只有一处）",
          r.status_code == 400, f"{r.status_code} {r.text[:160]}")
    # 12. 股息日历 —— 与导出是同一件事的**另一个出口**。上一轮堵掉的是导出那个口子，
    #     日历这个口子这一轮才堵上（改之前它直接查 DividendRecord，于是「只落流水、
    #     没有明细」的那条录入路径在日历里一条都不出现）。所以这一段与上面那节同形状：
    #     真的走一遍 ORM → 归集 → 分组 → 补名字，再和页面上的数字对账。
    r = session.get(f"{base}/analytics/calendar/", headers=headers)
    check("股息日历返回 200", r.status_code == 200, f"{r.status_code} {r.text[:200]}")
    cal = r.json() if r.status_code == 200 else {}
    check("股息日历是三组 + 合计的形状（旧的 {results: [...]} 已换掉）",
          all(k in cal for k in ("as_of", "upcoming", "received", "undated", "totals"))
          and "results" not in cal,
          json.dumps(sorted(cal), ensure_ascii=False)[:200])

    cal_upcoming = cal.get("upcoming") or []
    cal_received = cal.get("received") or []
    cal_undated = cal.get("undated") or []
    check("★ 只有流水的那笔股息也在日历里（照明细表写会整条漏掉）",
          any(item.get("origin") == "transaction" for item in cal_received),
          f"已到账 {len(cal_received)} 条，来源有：{sorted({i.get('origin') for i in cal_received})}")

    # 纯流水那条没有明细可查 → 明细字段必须是 null，不能是 0 / False
    flow_item = next((i for i in cal_received if i.get("origin") == "transaction"), {})
    check("★ 纯流水那条：明细字段是 null，不是 0 / False",
          flow_item.get("record_id") is None and flow_item.get("reinvested") is None
          and flow_item.get("ex_date") is None,
          json.dumps(flow_item, ensure_ascii=False)[:220])
    check("纯流水那条：金额 77.25、派息日 = 流水日期、账户名由视图补上",
          flow_item.get("amount") == "77.25" and flow_item.get("pay_date") == "2026-08-20"
          and flow_item.get("account_name") == "冒烟账户"
          and flow_item.get("transaction_id"),
          json.dumps(flow_item, ensure_ascii=False)[:220])

    totals = cal.get("totals") or {}

    def _bucket_sum(*maps):
        out = {}
        for m in maps:
            for cur, amt in (m or {}).items():
                out[cur] = out.get(cur, Decimal("0")) + Decimal(str(amt))
        return out

    parts = _bucket_sum(totals.get("upcoming_by_currency"), totals.get("received_by_currency"),
                        totals.get("undated_by_currency"))
    check("★ 日历三组逐币种相加 == 合计（漏掉一整组会当场少一块）",
          parts == {c: Decimal(str(a)) for c, a in (totals.get("by_currency") or {}).items()},
          f"分项相加={ {c: str(v) for c, v in parts.items()} }，合计={totals.get('by_currency')}")
    check("日历的条目数对得上（三组之和 == totals.count）",
          len(cal_upcoming) + len(cal_received) + len(cal_undated) == totals.get("count"),
          f"{len(cal_upcoming)}+{len(cal_received)}+{len(cal_undated)} vs count={totals.get('count')}")

    # 与页面上的数字对账 —— 和导出那节同一个道理：把「日历与累计股息同口径」
    # 从一句话变成可执行的检查。两边都按全部年份比，所以不传 year。
    r = session.get(f"{base}/analytics/summary/", headers=headers)
    page_total = report.field(r.json(), "dividend_total")
    calendar_total = sum((Decimal(str(a)) for a in (totals.get("by_currency") or {}).values()),
                         Decimal("0"))
    check("★ 日历合计 == 总览的累计股息（同一口径，不是各算一遍）",
          page_total is not None and Decimal(str(page_total)) == calendar_total,
          f"页面 dividend_total={page_total!r}，日历合计={calendar_total}")

    r = session.get(f"{base}/analytics/calendar/", params={"year": "2026"}, headers=headers)
    y_cal = r.json() if r.status_code == 200 else {}
    y_count = (y_cal.get("totals") or {}).get("count")
    check("日历认 year 筛选（与图表/导出同一套判据）",
          r.status_code == 200 and y_count == totals.get("count"),
          f"{r.status_code} count={y_count}（全部年份 {totals.get('count')}）")
    r = session.get(f"{base}/analytics/calendar/", params={"year": "2020"}, headers=headers)
    cal_2020 = r.json() if r.status_code == 200 else {}
    check("日历认 year 筛选（2020 一年都没有，三组全空）",
          r.status_code == 200
          and (cal_2020.get("totals") or {}).get("count") == 0
          and not any(cal_2020.get(k) for k in ("upcoming", "received", "undated")),
          f"{r.status_code} {r.text[:160]}")

    r = session.get(f"{base}/analytics/calendar/", params={"year": "abc"}, headers=headers)
    check("日历的 year 写错也回 400（解析只有一处）",
          r.status_code == 400, f"{r.status_code} {r.text[:160]}")




if __name__ == "__main__":
    sys.exit(run(body))
