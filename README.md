# 资产账本 asset-ledger

股息视角的多市场投资资产记录。**原生鸿蒙（ArkTS）客户端 + Django 后端 + MySQL**，支持 A 股 / 港股 / 美股 / 基金 / 外汇 / 数字货币。

仓库：https://github.com/virtualman333/asset-ledger

## 它解决什么

- 手动记账太烦 —— 券商截图、成交短信直接丢给 Agent，识别成草稿，一键确认入账
- 价格靠手查 —— 后端定时抓行情，持仓浮盈自动更新
- 收益算不清 —— 移动加权成本 + XIRR 年化 + 多币种折算，口径写死在引擎里
- 股息没人管 —— 股息记录、月度被动收入、股息日历单独成模块

## 设计红线

1. **AI 只生成草稿，人确认才入账。** 识别结果永远进草稿箱，低置信度或缺字段强制标红补全。
2. **流水是唯一事实源。** 持仓、成本、盈亏、股息全部由流水推导，不存第二份真相。
3. **原始凭证永不硬删。** 每笔 Agent 入账的流水都能回溯到截图与识别原文。

### 股息口径（只有一个）

股息有两条合法录入路径，最容易变成「同一笔钱算两遍」或「两个页面数字不一样」：

| 录入方式 | 落库 | 金额口径 |
| --- | --- | --- |
| `POST /transactions/dividends/` | 股息明细（DividendRecord） | 税后净额 `net` |
| `POST /transactions/records/` side=DIVIDEND | 一条 DIVIDEND 流水 | 现金变动 |
| 两者都落并互相关联 | 明细挂在流水之上 | `docs/DESIGN.md` §3 的形态 |

归集规则写在 **`server/apps/analytics/dividend_income.py`（唯一定义处）**：

1. 每条股息明细计一次，金额取 `net`；它关联的那条流水标记为「已被明细代表」。
2. 没有被任何明细代表的 DIVIDEND 流水计一次，金额取 `abs(amount)`。
3. 所以第三种形态不会重复计，前两种都算数。

持仓明细、总览统计、股息月度分布三处**都调用这份归集**，任何一处都不许自己再算一遍。

## 技术栈

| 层 | 选型 |
| --- | --- |
| 客户端 | HarmonyOS NEXT（API 12+）ArkTS + ArkUI，relationalStore 缓存，品牌紫 `#7166F0` |
| 后端 | Django 5.2 + DRF + SimpleJWT，MySQL 8.4，APScheduler 调度 |
| 行情 | 腾讯（A/港/美）、OKX（加密）、Yahoo（美股备用）、Frankfurter + ER-API（汇率） |
| Agent | 多模态 LLM（OpenAI 兼容接口），无 Key 时降级为规则解析 |

## 目录结构

```
harmony/   鸿蒙工程（DevEco Studio 打开这一层）
server/    Django 后端
docs/      方案设计与接口文档
scripts/   冒烟测试等工具脚本
```

## 客户端页面

| Tab | 能力 |
| --- | --- |
| 持仓 | 按账户聚合的持仓、成本、现价、浮盈、已实现盈亏、累计股息 |
| 记一笔 | 买入 / 卖出 / 股息 / 入金 / 出金，标的不存在时按「市场+代码」自动创建并复用 |
| 收件箱 | Agent 识别出的草稿，确认才入账，可丢弃，原始凭证可溯源 |
| 统计 | 总资产、成本、浮盈、总收益、XIRR 年化、股息月度分布 |
| 我的 | 退出登录 |

## 快速开始

后端：

```bash
cd server
python -m venv .venv
.venv/Scripts/pip install -r requirements.txt   # Windows
cp .env.example .env                            # 按需改数据库与 LLM Key
python manage.py migrate
python manage.py seed_demo                      # 造演示数据：账户/标的/流水/行情
python manage.py runserver 0.0.0.0:8000
```

冒烟测试（后端已启动）：

```bash
python scripts/smoke_api.py             # 覆盖注册→记账→行情→统计→Agent 识别→入账
python scripts/smoke_record_flow.py     # 覆盖鸿蒙「记一笔」页：标的自动建→买卖→股息→出入金→持仓推导→股息口径一致
```

单元测试（纯 Python，**不需要数据库、不需要 Django**）：

```bash
cd server
python -m unittest discover -s apps/analytics/tests -t .
```

股息口径的归集规则（`apps/analytics/dividend_income.py`）刻意不 import django，就是为了让这条最影响账目、又最容易写错的规则能被秒级验证。

演示账号：`demo / demo12345`

客户端：用 DevEco Studio 打开 `harmony/`，跑起来后在登录页填后端地址：

| 运行环境 | 地址 |
| --- | --- |
| 本地模拟器 | `http://10.0.2.2:8000/api/v1`（默认已填） |
| 真机 | `http://<电脑局域网IP>:8000/api/v1`，手机与电脑同 WiFi |

调试包已放行明文 HTTP，正式包必须走 HTTPS。

## 主要接口

```
POST /api/v1/auth/register|token|token/refresh    认证
GET/POST /api/v1/accounts/                         账户
GET/POST /api/v1/assets/                           标的
GET/POST /api/v1/transactions/records/             流水（支持 client_request_id 幂等）
GET/POST /api/v1/transactions/dividends/           股息
GET  /api/v1/market/quotes/?asset_ids=1,2&refresh=1 行情
GET  /api/v1/market/fx/?base=USD&quote=CNY         汇率
POST /api/v1/ingest/image|text                     凭证上传与识别
GET  /api/v1/ingest/drafts/                        草稿箱
POST /api/v1/ingest/drafts/{id}/confirm|discard    确认入账 / 丢弃
GET  /api/v1/analytics/positions|summary|dividends|calendar
```

`analytics/summary/` 里的收益口径：

| 字段 | 含义 |
| --- | --- |
| `dividend_total` | 累计股息（所有已录入的股息，不分年份） |
| `dividend_annual` | 近 365 天股息 —— 用滚动一年而不是自然年，免得 12 月建的账本次年 1 月显示成 0 |
| `dividend_yield` | 股息率 = `dividend_annual` ÷ 持仓成本；**成本为 0（已清仓）时是 `null` 而不是 0** |
| `monthly_passive_income` | 月度被动收入 = `dividend_annual` ÷ 12 |
| `annualized` | XIRR 年化。出入金与**收到的股息现金**都算现金流（股息不体现在市值里，漏掉会把年化算低） |

`analytics/positions/` 每一行也带 `dividend_total` / `annual_dividend` / `dividend_yield`。

## 路线图

- [x] M0 骨架：MySQL 建库、后端骨架、鸿蒙工程、GitHub 仓库
- [x] M1 手动记账闭环：账户 / 标的 / 流水 CRUD + 幂等
- [x] M2 行情与收益：多源抓价、持仓、浮盈、XIRR 多币种折算
- [x] M3 股息模块：股息记录、月度聚合、日历
- [x] M4 Agent 链路：文本与截图识别 → 草稿箱 → 确认入账
- [ ] M5 打磨：截图上传端上接入、图表、导出 CSV、通知、真机签名

## 已知约束

- 鸿蒙端目前只做文本凭证提交，截图上传接口后端已就绪，端上接入在 M5
- **股息率与月度被动收入后端已提供**（`analytics/summary/`），鸿蒙统计页尚未展示，端上接入在 M5
- 美股行情延迟约 15 分钟（腾讯/Yahoo 免费源），需要实时行情请接付费源
- 股息数据以手动录入与 Agent 识别为主，自动股息日历仍在 TODO
