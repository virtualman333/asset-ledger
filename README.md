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

写 `PriceQuote`（行情快照）的地方只有一处：**`server/apps/market/services.py`** 的
`refresh_quote_map()`（单只的情形走 `refresh_quote()`，两者共用同一份「查缓存 → 取价 → 落库」）。
它同时负责「缓存期内直接复用」的判据，三种触发方式都从这里进：

| 触发方式 | 场景 |
| --- | --- |
| 进程内定时任务（APScheduler） | 默认行为，`QUOTE_REFRESH_MINUTES` 决定间隔，默认 15 分钟；`0` 表示关闭 |
| `python manage.py refresh_quotes [--force] [--batch-size N]` | 不想在服务进程里挂线程时，交给 cron / 宝塔计划任务 |
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

**一轮的请求条数才是限流的实际口径，所以 A 股 / 港股 / 美股是合并成一条请求抓的。**
腾讯接口本来就吃逗号分隔的多代码（`q=sh600000,sz000001,hk00700,usAAPL,sh000001`，
实测一条请求 0.16 秒回来 5 行），而在这之前是**逐只请求**：60 只标的一轮 60 条，
多机部署再乘一遍 —— 正是下面那句「轮询太快会被源限流」所预告的压力，等于自己撞上去。
现在每 60 个代码一条请求（`--batch-size 1` 可退回逐只，排查用），其余市场逐只问各自的源。
这一轮真的发了几条 HTTP 会出现在日志与命令输出里：

```
python manage.py refresh_quotes
行情刷新：成功 8、缓存内跳过 0、失败 1（HTTP 3 次，分 1 批）
```

代码规范化与批量解析单独放在 `apps/market/tencent.py`（纯函数，不 import django，
于是「一批只发一条请求」可以离线断言）。腾讯接口有三个实测出来的坑写在那儿的模块说明里：

- **区分大小写**：`usAAPL` 有价，`usaapl` / `usAaPl` / `HK00700` / `SH600000` 一律返回
  `v_pv_none_match="1";` —— 不是 404、不是报错行，就是没这只标的。
- **认不出的代码整行不出现**，所以只能按行自己带的代码回填，照着请求顺序对齐必然错位。
- **全不认时回来的是唯一一行兜底**（`v_pv_none_match`），它不是标的。

`USB`（美国合众银行）这种**本身就带 `us` 两个字母**的真实代码是前两条的交汇点：它和
「`us` 前缀 + ticker `B`」在字符串上无法区分。所以规则是：`us`/`US` 后面接**全大写且 ≥2 位**
才当前缀（`usAAPL`、`USAAPL`、`usBRK.B`），其余当裸 ticker（`USB` → `usUSB`），
两头都不像的（`usaapl`、`usB`）返回 `None` 并记一条 warning ——
**少一个价，好过悄悄记一个错价。** 改动之前这两个写法都会被整串 `lower()`：
`usAAPL` → `usaapl`、`USB` → `usb`，两个都是查不到、也不报错的代码。


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

冒烟测试（**默认自启一个只服务当前代码的服务端**，跑完关掉）：

```bash
python scripts/smoke_api.py             # 覆盖注册→记账→行情→统计→Agent 识别→入账
python scripts/smoke_record_flow.py     # 覆盖鸿蒙「记一笔」页：标的自动建→买卖→股息→出入金→持仓推导→股息口径一致
```

要验一个**已经在跑**的服务（比如部署到服务器之后），用 `--base` 指过去
（`AL_SMOKE_BASE` 环境变量等价）：

```bash
python scripts/smoke_record_flow.py --base http://127.0.0.1:8000
```

这两条命令原先各自写死 `127.0.0.1:8000`，于是「脚本验的是这份代码」全靠「那个端口上的
进程恰好是这份代码」这个约定撑着，而脚本对它一无所知：谁起的、什么时候起的、跑的是哪个
提交。实测撞上过一个两天前起的旧进程：它的 `/analytics/summary/` 还是旧字段，26 条检查里
5 条 FAIL —— **全是假缺陷**；随后脚本在 `summary["dividend_yield"]` 上 KeyError 中断，
连失败小结都没打印出来。一次误诊，加一次「没有结论」。所以改成两条一起用：

1. **默认自启**：不给 `--base` 就自己起一个只服务当前 checkout 的服务端 —— 「测的是这份
   代码」由构造保证，不靠约定。
2. **`--base` 必须先自证**：脚本先要一次 `/health/`，拿服务端**自己那份源码的内容指纹**与
   本地现算一遍比对。一致才继续；对不上、或者对面压根没有这个端点（= 旧版本），当场记一条
   FAIL 并写明「**下面的失败不能当缺陷读**」—— 声明拦不住误诊，比对才拦得住。
   **自启模式也走同一条核对**，于是「起的是当前 checkout」也从「由构造保证」变成了
   「被检查过」。

两个脚本共用 `scripts/_smoke_lib.py`，它另外兜住一件事：body 里抛出的任何异常都折算成
一条 FAIL，退出码永远由检查结果决定 —— 冒烟脚本不允许以「没有结论」收场。这些性质由
`apps/core/tests/test_smoke_lib.py` 钉住（含指纹核对五种结局各走一遍），指纹本身的口径由
`apps/core/tests/test_source_stamp.py` 钉住，连同「`scripts/` 里的脚本」与「本节点名的脚本」
的双向一致（新脚本忘了写文档、或文档点着不存在的脚本，都会红）。

### 服务端自证（`/api/v1/health/`）

`GET /api/v1/health/`（**不需要认证**）报出服务端自己那份源码的内容指纹、它的启动时刻，
以及「进程起来之后源码又被改过吗」：

```json
{"service": "asset-ledger", "pid": 1234, "started_at": "2026-09-17T03:09:47Z",
 "uptime_seconds": 12.3,
 "source": {"files": 93, "fingerprint": "bc3bdf4d…", "newest_file": "config/urls.py",
            "newest_mtime": "2026-09-17T03:07:12Z", "unreadable": [],
            "stale": false, "stale_files": []}}
```

两个机制都要，少一个就有一种误判漏过去：

- **指纹**（相对路径 + 内容一起入哈希）答「是不是同一份代码」。入哈希前**行尾统一成 LF** ——
  这不是可选的口味问题：同一份代码在 Windows（CRLF）与 Linux（LF）上必须算出同一个数，
  否则跨机核对必然误报成「不同」。非 `.py`、`__pycache__`、`.venv` / `venv`（那里可能躺着
  **另一份**被装进去的代码）都不算。
- **`stale`** 答「这个进程是不是还在跑它自己那份代码」：先改文件、再发请求，指纹算的是磁盘上
  **新**的内容，而进程跑的是**旧**代码 —— 只看指纹会把这一种判成「没问题」。这条只能服务端
  自己判（两边各拿自己的时间戳比会撞上时钟偏差），所以启动时刻取的是**进程刚开始跑的那一刻**
  （`AppConfig.ready()`），不是「第一个请求」。

它暴露的信息只有「这确实是 asset-ledger」和源码的一个内容哈希 —— 源码本来就公开在仓库里；
**绝对路径、`SECRET_KEY`、数据库连接串一律不放**。

自启的进程连的是 `server/.env` 那个库，所以它会**真的写库**，和手工点一遍「记一笔」等价。

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

行情代码的规范化与批量解析（`apps/market/tencent.py`）同理不 import django：
上面说的那三条接口坑（区分大小写、认不出的行不出现、兜底行不是标的）写错了都只表现为
**静默少一个价**，所以样本直接用真实响应原文（`tests/tencent_fixtures.py`），
并且「一批只发一条请求」是靠**注入假传输数它被调了几次**来断言的，不出网、秒级。

唯一的例外是 `apps/market/tests/test_scheduler_autostart.py`：它要
`django.conf.settings.configure` 才能把 `autostart()` 真调起来。**没装 Django 时它整条跳过**
（报告里是 `OK (skipped=1)`，不是 `FAILED`），装上 `requirements.txt` 之后会真的跑 ——
上面那条命令的承诺是「不需要 Django 也能跑」，跳过才对得起这句话。

这句承诺现在**真的跑一遍来钉住**（`apps/core/tests/test_no_django_required.py`）：在一个
「`import django` 会失败」的子进程里把整套测试重新跑一遍，要求 `OK (skipped=1)`。理由是
装了依赖的开发机上必然有 Django，任何一个测试模块多一句 `from django...` 在那里都照常
全绿 —— 只有照着本文档在一台裸 Python 上跑的人才会撞上一片 ERROR。多一个模块需要 Django，
`skipped` 就变成 2，这条检查立刻红。

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
GET  /api/v1/health/                               版本自证（源码指纹，无需认证）
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
- 一轮的请求条数才是限流的实际口径：A 股 / 港股 / 美股**合并成一条请求**（每 60 个代码一条，
  腾讯接口本身支持多代码），其余市场逐只问各自的源。`manage.py refresh_quotes` 会把这一轮
  真的发了几条 HTTP 打出来
- **多 worker 部署**（`gunicorn -w 4`）时只有抢到文件锁的那一个 worker 会真的抓行情，
  其余进程只跑 Web 请求 —— 这是刻意的，否则每轮会被放大成 N 遍。
  多机部署时每台机器各有一个调度器（锁是**本机**的，别放到网络盘上）
- 股息数据以手动录入与 Agent 识别为主，自动股息日历仍在 TODO
- `/api/v1/health/` 的指纹在入哈希前把行尾统一成 LF，所以**「只改了行尾」不算改动** ——
  这是刻意的（同一个提交在 Windows 与 Linux 上必须是同一个指纹），代价是它答不了
  「行尾有没有被改坏」这类问题
