"""Agent 凭证抽取。

设计约定：
1. LLM 输出必须落在 classify_schema() 定义的结构里，拿不到就标 missing，绝不自作主张补全。
2. 没有 LLM Key 时降级为规则解析，置信度一律压到 0.5 以下，强制人工确认。
"""
from __future__ import annotations

import base64
import json
import logging
import re
from decimal import Decimal

from django.conf import settings

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """你是一个投资流水结构化助手。从用户提供的券商截图或文本中抽取交易信息，只输出 JSON，不要解释。
字段：
side: BUY/SELL/DIVIDEND/DEPOSIT/WITHDRAW 之一
symbol: 标的代码，如 600519、AAPL、BTC-USDT
market: A/HK/US/CRYPTO/FUND/FOREX/OTHER
name: 标的名称，可为空字符串
quantity: 数量（数字）
price: 单价（数字）
fee: 手续费（数字，没有就 0）
tax: 税费（数字，没有就 0）
currency: CNY/USD/HKD/USDT 等
amount: 现金变动金额（数字，不带货币符号；账户进钱为正、出钱为负）——买入填负数、卖出填正数、入金填正数、出金填负数；不确定就留空
traded_at: ISO8601 时间，含时区
account_hint: 券商或账户名，可为空
confidence: 0-1 的置信度
missing: 无法确定的字段名数组
识别不到就返回 {"side":null,"confidence":0,"missing":["all"]}"""


def classify_schema() -> dict:
    return {
        "side": None,
        "symbol": None,
        "market": None,
        "name": "",
        "quantity": None,
        "price": None,
        "fee": 0,
        "tax": 0,
        "currency": "CNY",
        "amount": None,
        "traded_at": None,
        "account_hint": "",
        "confidence": 0,
        "missing": [],
    }


def _llm_chat(messages: list, model: str) -> dict | None:
    import requests

    if not settings.LLM_API_KEY:
        return None
    try:
        resp = requests.post(
            f"{settings.LLM_BASE_URL.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {settings.LLM_API_KEY}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages, "response_format": {"type": "json_object"}, "temperature": 0},
            timeout=60,
        )
        content = resp.json()["choices"][0]["message"]["content"]
        return json.loads(content)
    except Exception as exc:
        logger.warning("llm call failed: %s", exc)
        return None


def extract_from_image(image_path: str) -> tuple[dict, str]:
    """截图抽取。返回 (结果, provider)。"""
    with open(image_path, "rb") as fh:
        b64 = base64.b64encode(fh.read()).decode()
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "请从这张券商/钱包截图中抽取交易信息。"},
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}},
            ],
        },
    ]
    data = _llm_chat(messages, settings.LLM_VISION_MODEL)
    if data:
        return _normalize(data), "llm"
    return classify_schema(), "none"


def extract_from_text(text: str) -> tuple[dict, str]:
    data = _llm_chat(
        [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"文本内容：\n{text}"}],
        settings.LLM_TEXT_MODEL,
    )
    if data:
        return _normalize(data), "llm"
    return rule_extract(text), "rule"


def _normalize(data: dict) -> dict:
    base = classify_schema()
    base.update({k: v for k, v in data.items() if k in base})
    for key in ("quantity", "price", "fee", "tax", "amount"):
        value = base.get(key)
        if value in (None, ""):
            continue
        try:
            base[key] = str(Decimal(str(value)))
        except Exception:
            base[key] = None
    try:
        base["confidence"] = float(base.get("confidence") or 0)
    except Exception:
        base["confidence"] = 0
    base["missing"] = base.get("missing") or []
    return base


def rule_extract(text: str) -> dict:
    """无 LLM 时的规则兜底：只覆盖最常见格式，置信度固定偏低，强制人工确认。"""
    result = classify_schema()
    lowered = text.replace(" ", "")
    if "买入" in lowered or "证券买入" in lowered:
        result["side"] = "BUY"
    elif "卖出" in lowered or "证券卖出" in lowered:
        result["side"] = "SELL"
    elif "分红" in lowered or "派息" in lowered or "股息" in lowered:
        result["side"] = "DIVIDEND"
    elif "入金" in lowered or "转入" in lowered or "充值" in lowered:
        result["side"] = "DEPOSIT"
    elif "出金" in lowered or "转出" in lowered or "提现" in lowered:
        result["side"] = "WITHDRAW"

    if result["side"] in ("DEPOSIT", "WITHDRAW"):
        # 出入金没有标的、数量、单价。金额猜错就是一笔错账，而它又是年化现金流
        # 的唯一来源 —— 所以这里只认方向，金额留给用户补（确认入账时缺金额会
        # 明确回 400，而不是像以前那样静默记成 0）。
        result["confidence"] = 0.3
        result["missing"] = [k for k in ("amount", "traded_at") if not result.get(k)]
        return result

    # 中文与数字同属 \w，\b 不生效，必须用前后断言
    symbol = re.search(r"(?<!\d)(\d{6})(?!\d)", lowered) or re.search(r"\b([A-Z]{1,5})\b", text)
    if symbol:
        result["symbol"] = symbol.group(1)
        result["market"] = "A" if symbol.group(1).isdigit() and len(symbol.group(1)) == 6 else "US"
    qty = re.search(r"(\d+(?:\.\d+)?)\s*(?:股|份|张)", lowered)
    if qty:
        result["quantity"] = qty.group(1)
    price = re.search(r"(?:价格|成交价|单价)[:：]?\s*(\d+(?:\.\d+)?)", lowered) or re.search(r"(\d+(?:\.\d+)?)\s*元", lowered)
    if price:
        result["price"] = price.group(1)
    result["confidence"] = 0.4 if result["side"] and result["symbol"] else 0.1
    result["missing"] = [k for k in ("side", "symbol", "quantity", "price", "traded_at") if not result.get(k)]
    return result
