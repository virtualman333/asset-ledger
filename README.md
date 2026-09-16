# 资产账本 asset-ledger

股息视角的多市场投资资产记录。**原生鸿蒙（ArkTS）客户端 + Django 后端 + MySQL**，支持 A 股 / 港股 / 美股 / 基金 / 外汇 / 数字货币。

仓库：https://github.com/virtualman333/asset-ledger

## 它解决什么

- 手动记账太烦 —— 券商截图、成交短信直接丢给 Agent，识别成草稿，一键确认入账
- 价格靠手查 —— 后端每 15 分钟自动抓一轮行情，持仓浮盈自己更新
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

### 行情是怎么更新的（写入口也只有一个）

写 `PriceQuote`（行情快照）的地方只有一处：**`server/apps/market/services.py` 的 `refresh_quote()`**。
它同时负责「缓存期内直接复用」的判据，三种触发方式都调它：

| 触发方式 | 场景 |
| --- | --- |
| 进程内定时任务（APScheduler） | 默认行为，`QUOTE_REFRESH_MINUTES` 决定间隔，默认 15 分钟；`0` 表示关闭 |
| `python manage.py refresh_quotes [--force]` | 不想在服务进程里挂线程时，交给 cron / 宝塔计划任务 |
| `GET /market/quotes/?asset_ids=1,2&refresh=1` | 端上主动拉一次（客户端当前不调，保留给调试与将来的下拉刷新） |

**一台机器上只有一个进程会真的去抓。** 判据分两层：

- 「这个进程该不该起任务」是纯函数（`market/quote_schedule.py`）：间隔为 0 不起、
  一次性命令不起、`runserver` 的 autoreload 只让子进程起。
- 「这一台机器上是不是已经有别人在抓」交给一把文件锁（`market/quote_lock.py`，
  默认 `server/var/quote_refresh.lock`，可设 `QUOTE_REFRESH_LOCK` 改位置）。
  因为 `gunicorn -w 4` 这种多 worker 部署下，每个 worker 都会 import 一次 WSGI 应用、
  各自跑一遍 `AppConfig.ready()`，而调度器的 `max_instances=1` **只在单个调度器内部生效**。
  实测修之前是 4 / 4 个 worker 各起一个调度器 —— 正好撞上下面「轮询太快会被源限流」那条。
  锁在进程退出（包括 `kill -9`）时由操作系统自动释放，不需要超时与清理。

**顺序是先判据、后抢锁。** 反过来的话，`runserver` 那个只负责看门的父进程会先把锁拿走
再放弃起任务，真正干活的子进程永远抢不到 —— 从「每轮抓两遍」变成「一遍都不抓」。

为什么值得单独写一段：这句话（「后端定时抓行情」）在 README 上挂了很久，但代码里
**没有任何持续的机制会去写行情表** —— 只有手工调接口（客户端从来不调它）和一次性的
`seed_demo`。实测库里最后一批快照是 `seed_demo` 在 2026-09-16 16:55 同一秒写下的 4 条，
此后 8 小时一格没动；没跑过演示数据的账号则连一条都没有，现价恒为空、总资产恒 `0.00`。
所以现在「定时任务真的存在」「写入口只有一处」「多 worker 也只起一个」都各有一条单测锁着
（`apps/market/tests/test_quote_schedule.py`、`test_quote_lock.py`、`test_scheduler_autostart.py`）。


## 技术栈

| 层 | 选型 |
| --- | --- |
| 客户端 | HarmonyOS NEXT（API 12+）ArkTS + ArkUI，relationalStore 缓存，品牌紫 `#7166F0` |
| 后端 | Django 5.2 + DRF + SimpleJWT，MySQL 8.4，APScheduler（进程内定时抓行情，见上） |
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

`runserver` 起来之后行情就由进程内的定时任务接管（每 15 分钟一轮）。不想让它挂在服务
进程里，就把 `QUOTE_REFRESH_MINUTES=0` 写进 `.env`，改用命令行 + 系统定时器：

```bash
python manage.py refresh_quotes          # 抓一轮（缓存期内的标的会跳过）
python manage.py refresh_quotes --force  # 忽略缓存，强制重抓
```

冒烟测试（后端已启动）：

```bash
python scripts/smoke_api.py             # 覆盖注册→记账→行情→统计→Agent 识别→入账
python scripts/smoke_record_flow.py     # 覆盖鸿蒙「记一笔」页：标的自动建→买卖→股息→出入金→持仓推导→股息口径一致
```

单元测试（纯 Python，**不需要数据库、不需要 Django**）：

```bash
cd server
python -m unittest discover -s apps -t .
```

两条最容易写错、又最影响账目的规则刻意不 import django，就是为了让它们能被秒级验证：股息归集（`apps/analytics/dividend_income.py`）与流水金额口径（`apps/transactions/amount_rules.py`）。

这两处出错的方式都是**静默**的：前者会让同一笔股息算两遍，后者会让一笔入金记成 0 —— 界面上都看不出异常，只有数字悄悄错了。

第三条同样不 import django：持仓估值（`apps/analytics/valuation.py`）。它错的方式是
**「拿不到报价」被当成「不值钱」** —— 总资产偏小、年化变成巨额负收益，也一样不报错。
定时抓行情该不该启动的判据（`apps/market/quote_schedule.py`）同理，可注入假的调度器来断言。

那条判据管不着的另一半（**多 worker 部署会不会各抓一遍**）在
`apps/market/quote_lock.py`：标准库文件锁，同样不 import django、不连库。
它的断言刻意落在真实的 OS 锁状态上（抢两次、放一次再抢、真开三个进程同时抢），
因为这条判据唯一会错的方式就是「以为排他了，其实没有」—— 注入假锁只能测出「有没有调用」。

### 流水的现金变动（`amount`）

`amount` = 账户现金变动，**正数 = 资金流入、负数 = 资金流出**（已含 `fee` / `tax`）。
推导与符号归一只有一处：`server/apps/transactions/amount_rules.py`。

| side | 没给 `amount` | 给了 `amount` |
| --- | --- | --- |
| `BUY` | `−(数量×单价 + fee + tax)` | 原样 |
| `SELL` / `DIVIDEND` / `FEE` / `TAX` | `数量×单价 − fee − tax` | 原样 |
| `SPLIT` | `0`（拆股不产生现金变动） | 原样 |
| `DEPOSIT` / `WITHDRAW` | **回 400**（没有数量×单价可推） | 按 side 归一符号；为 0 也回 400 |

出入金为什么必须显式给金额：它是 XIRR 现金流的两端，而「猜」出来的 0 会让这笔钱在年化里凭空消失、且全程不报错。方向由 `side` 唯一决定，所以 `amount` 只表达大小 —— 「出金 50000」填成正数也会被归一为 `−50000`。

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
| `market_value` | **只含拿到报价的持仓**。拿不到报价的那部分不在这个数里 |
| `unpriced_cost_basis` | 有数量但拿不到报价的持仓成本（已折算），与 `market_value` 相加才是「此刻大约值多少」 |
| `unpriced_symbols` | 上面那些持仓的标的代码，用来告诉用户是哪几个 |
| `valuation_note` | 一句话解释上面的数字：有持仓没报价、或年化为什么算不出来；一切正常时是 `null` |

没报价的持仓**按成本计入**总资产与 XIRR 终值（口径写在 `apps/analytics/valuation.py`，唯一定义处）：
按 0 计等于宣布这笔钱蒸发了，年化会给出一个巨额负收益 —— 而这正是它以前的行为，且只在
「拿不到报价」时才发生，很难被撞见。取成本的代价是这部分盈亏未知，所以 `valuation_note`
必须点名是哪几个标的，页面上那句话不是装饰。

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
- 免费源没有推送，只能按 `QUOTE_REFRESH_MINUTES` 轮询（默认 15 分钟）；轮询太快会被源限流，
  拿不到价时会退回上一次的旧价并把 `stale` 标出来，不会写空记录
- **多 worker 部署**（`gunicorn -w 4`）时只有抢到文件锁的那一个 worker 会真的抓行情，
  其余进程只跑 Web 请求 —— 这是刻意的，否则每轮会被放大成 N 遍。
  多机部署时每台机器各有一个调度器（锁是**本机**的，别放到网络盘上）
- 股息数据以手动录入与 Agent 识别为主，自动股息日历仍在 TODO
