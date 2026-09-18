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

这张表不是手抄完就算：`apps/core/tests/test_client_pages_contract.py` 把它与
`harmony/entry/src/main/ets/pages/Index.ets` 里的 `tabBar(this.tabItem('持仓', 0))` 这样的
调用**双向**对齐 —— 名字逐个相等、顺序必须相同、下标必须是 `0..n-1` 连续（跳号在鸿蒙里
表现为「点了没反应」）。同一个检查还顺带钉住每个页面文件都被 `Index.ets` import、
每条本地 import 都落到磁盘、import 进来的组件真的被用到、`main_pages.json` 登记的页面
都存在且入口页在里面。改 Tab 栏就得改这张表，反之亦然，否则自测红。

## 客户端打了哪些接口

客户端 `.ets` 里打出去的每个路径，都由 `apps/core/tests/test_client_route_contract.py`
对着服务端**真路由表**核一遍。真值不是手抄的：`apps/core/route_inventory.py` 用 AST 读
`config/urls.py` 与各 app 的 `urls.py`（不 import Django、不连库），README 那份手抄的
「主要接口」清单用的也是它 —— 一份解析器，两个消费者。

这一面原先完全没人看，而它是最看不见的一面：本机没有 DevEco / hvigor 工具链，`.ets`
**编译不了**，路径写错不会在这里报任何错；就算上了真机，后果也只是那个页面永远转圈
（404 被界面吃成一句「加载失败」）。更糟的是接口改名 —— 代码侧一切正常，只有页面在转圈。

解析面用**两遍扫描逐文件对账**：宽扫认得出任何 `ApiClient.<动词>(` 的调用形态（泛型、
跨行都算），窄扫只认紧跟其后的字面量路径，**两者命中数必须相等**。某一处路径换成了变量，
那一处就是「没被检查」，不该因为同文件别处读得出来而被放过 —— 这是负向验证当场教出来的：
第一版只要求「窄扫至少一条」，把其中一个路径换成变量时它照样绿。

只钉一个方向，是刻意的：客户端打的路径必须存在；服务端存在的路由客户端有没有打，**不查**
（接口给别的客户端用、或还没有页面接入，都不是缺陷）。

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
python scripts/smoke_api.py             # 覆盖注册→记账→行情→统计→Agent 识别→入账→导出 CSV
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

### 导入流水（CSV）

`POST /api/v1/transactions/records/import/` 把 CSV 批量导成流水 —— 券商导出的成交明细、
在 Excel 里补好的一批记录，不用一条条手敲。**认的列名就是导出用的那一份**
（`CSV_COLUMNS`，中文表头或英文键名都行），所以「自己导出的文件自己必然导得回来」
是一条能被测出来的性质，而不是一句承诺：`apps/transactions/tests/test_import_rules.py`
里那条往返用例把 12 种流水（每一种 side + 五种容易坏的备注）导出再导入，逐字段相等。

传法两种，取其一：

| 方式 | 怎么给 |
| --- | --- |
| 文件上传 | `multipart/form-data`，字段名 `file`（必须是 UTF-8；Excel 里选「CSV UTF-8（逗号分隔）」另存） |
| 整份文本 | JSON 里给 `csv`：`{"csv": "时间,方向,账户,币种\r\n…"}`（端上拿不到文件选择器时更省事） |

可选参数：`dry_run`（真值写法 `1` / `true` / `yes` / `on` 都认）只做校验不落库；
`client_request_id_prefix` 给每行一个幂等键（`<前缀>-L<行号>`），于是**同一份文件传两次
不会记两遍**，且不需要改文件内容。

必需的列 4 个：`时间` `方向` `账户` `币种`。其余（标的代码 / 数量 / 单价 / 现金变动 /
手续费 / 税费 / 入账汇率 / 备注）缺了按空处理 —— 空的是「没填」，不是 0。
`标的名称` 与 `来源` 两列**认得出但不消费**：来源恒为「导入」（把文件里的 `Agent 识别`
抄回来会让它去冒充一次从未发生过的识别），名称只作展示。

响应把三种结局分开报，因为**行级失败不整批回滚**（第 3 行写错了不该让第 4..200 行也进不来）：

```json
{"ok": false, "dry_run": false, "total": 12, "created": 10, "skipped": 0,
 "failed": [{"line": 4, "error": "账户「老账户」不存在 —— 导入只认本用户已有的账户，不会顺手新建"},
            {"line": 9, "error": "现金变动: 入金 / 出金必须填写金额 —— 它没有数量×单价可推，猜出来的 0 会让这笔现金流在年化里凭空消失"}]}
```

三条刻意这么定的口径：

- **文件级问题回 400，行级问题回 200。** 空文件、没有表头、列名认不出、缺必需列、
  超过 5000 行上限——这些跟你某一格写了什么无关，改文件即可，所以是 400。
  某一行内容不合法是 200 + `failed` 里那一条（行号按**文件里的行号**给，表头是第 1 行）。
- **不认识的列名一律报错，不静默忽略。** 「忽略读不懂的列」听起来宽容，代价是列名写错
  却拿到「成功导入 12 条」，而 12 条里没一条带着他想要的那个字段。
- **账户与标的按名字 / 代码在你自己名下查，查不到就是这一行的错误。**
  顺手建标的是另一种更坏的宽容：代码打错一位，账上多出一只没听过的持仓。代码在多个
  市场都存在时也报错（`Asset` 的唯一键是「市场+代码」，单靠代码不唯一）。

金额不在这里另算：每行都走 `TransactionSerializer`，也就是与 `POST /records/`
**同一套**校验与金额规则（`amount_rules.resolve_amount()`）。于是「入金没填金额必须报错」
「拆分不产生现金变动」这些口径在导入这条路径上自动成立，不需要第二份实现。

### 导出流水（CSV）

`GET /api/v1/transactions/records/export/` 把当前用户的流水导成 CSV，Excel / WPS 双击就能打开。

**筛选条件与 `GET /transactions/records/` 完全一致**（`from` / `to` / `side` / `account` / `asset` / `currency` / `source` / `search` / `ordering` 都认）。这不是「抄一份查询」，导出直接复用列表那套：`TransactionExportView` 拿 `TransactionViewSet` 的 `get_queryset()` 再走它的 `filter_queryset()`，三个 filter 后端一个不落地都在。于是「列表里看到 12 笔、导出只有 9 笔」这种漂移在结构上就不可能发生 —— 用户在列表里数一遍、导出再数一遍对不上，只会以为「少记了几笔」，不会想到是导出的筛选没跟上。分页对导出不生效：列表一页 50 条，导出是**全部**命中的流水。

`from` / `to` 写 `YYYY-MM-DD`，**两头都含当天**（`from=2027-01-01&to=2027-01-01` 就是那一天）；口径是「本地零点到次日零点」的半开区间，一天的宽度恰好 24 小时。日期写错、或者 `to` 早于 `from`，回 **400** 并把话说清楚。

这两个参数以前是另一副样子，值得写下来：实现用的是 `traded_at__date`，在 `USE_TZ = True` + MySQL 上**恒返回 0 条**——Django 会生成 `CONVERT_TZ(col, 'UTC', 'Asia/Shanghai')`，而 MySQL 的时区表（`mysql.time_zone_name`）**默认没装**，`CONVERT_TZ` 于是返回 NULL：不报错，只是一条都选不出来。本机实测 `CONVERT_TZ` 返回 `NULL`、8 条流水里 `__date` 区间命中 **0** 条。也就是说这条接口在装了时区表的机器上是好的、在本机是恒空的，**两边都不报错**（`docs/DESIGN.md` 第 6 节从 M0 起就承诺了 `?from=&to=`，一直没落点）。现在改成「把本地零点折成带时区的瞬间」再比，不依赖数据库的时区表；日期写错也从**500 变成 400**。

导出的列（顺序就是列序）：

| 列 | 说明 |
| --- | --- |
| 时间 | 发生时间，按 `TIME_ZONE`（Asia/Shanghai）格式化成 `YYYY-MM-DD HH:MM:SS` |
| 方向 | `side` 的中文名，取自模型自己的 `TextChoices` |
| 标的代码 | `asset.symbol`；入金 / 出金这类没有标的的流水留空 |
| 标的名称 | `asset.name` |
| 账户 | `account.name` |
| 数量 | `quantity` |
| 单价 | `price` |
| 现金变动 | `amount`（正数流入、负数流出，口径见上一节） |
| 手续费 | `fee` |
| 税费 | `tax` |
| 币种 | `currency` |
| 入账汇率 | `fx_rate` |
| 来源 | `source` 的中文名（手动 / Agent 识别 / 导入） |
| 备注 | `note` |

这张表不是手抄完就算：`apps/core/tests/test_export_contract.py` 把它与 `apps/transactions/export_rules.py` 的 `CSV_COLUMNS` **双向**对齐 —— 少一列、多一列、顺序换了都红。改列就得同时改两处。

三件已经替你处理掉的事。它们都不是口味问题，是「双击打开就知道了」：

- **带 UTF-8 BOM、行尾 CRLF。** 不带 BOM 时 Excel 会拿 GBK 去解 UTF-8，中文列头变成乱码；CRLF 是 RFC 4180 的规定，也是 Excel 的默认。
- **以 `=`、`+`、`-`、`@` 开头的单元格前面会多一个单引号。** 备注列来自手输与 Agent 对截图的识别，也就是说不完全由你掌控；不加这道处理，导出的文件在别人机器上打开就可能执行一段公式（DDE / 外部引用），而 CSV 本身看不出任何异常。代价是那一格会**多显示一个单引号**（`'=1+1`）—— 刻意的取舍，安全优先于好看。真正的负数（`-2500`）不受影响。
- **文件名给两份。** `filename=` 是纯 ASCII 的（给旧客户端），`filename*=UTF-8''…` 是中文名（现代浏览器取这条）。HTTP 头的值只能是 latin-1，把中文名直接写进 `filename=` 会让 Django 编码响应头时抛异常 —— 用户看到的只是「点了导出没反应」。

导出的内容**只有流水本身**：不导出原始凭证、不带 `client_request_id`，也不做任何聚合。股息有一份单独的导出，见下一节。

### 导出股息明细（CSV）

`GET /api/v1/analytics/dividends/export/` 把当前用户的股息导成 CSV。`?year=2026` 与 `GET /analytics/dividends/` **同口径**（按派息日筛）；不传就是全部年份。`year` 写错回 **400**（以前是 500），解析只有一处（`services.parse_year`），免得「图表按 2026 筛、导出按别的东西筛」。

**导的是「归集后的股息」，不是股息明细表。** 股息有两条合法录入路径（见第 20 行的「股息口径（只有一个）」）：走 `/transactions/dividends/` 的落一条明细，走 `/transactions/records/` 且 `side=DIVIDEND` 的**只落一条流水、没有明细**。所以「导出股息明细 = 把明细表列出来」这种最自然的写法会整条漏掉后一种 —— 而页面上的「累计股息」是两种都算的。结果就是：页面显示 810，导出的文件是空的，**两边都不报错**。这里与「累计股息」共用同一个归集函数（`services.dividend_entries`），页面有多少，文件里就有多少。

导出的列（顺序就是列序）：

| 列 | 说明 |
| --- | --- |
| 标的代码 | `asset.symbol` |
| 标的名称 | `asset.name` |
| 账户 | `account.name` |
| 币种 | `currency` |
| 除权日 | `ex_date`，只有股息明细里才有 |
| 派息日 | `pay_date` |
| 持股数 | `shares`，只有股息明细里才有 |
| 每股派息 | `amount_per_share`，只有股息明细里才有 |
| 税前 | `gross`，只有股息明细里才有 |
| 税费 | `tax`，只有股息明细里才有 |
| 税后 | 归集口径的到手金额（有明细取 `net`，纯流水取流水金额的绝对值） |
| 分红再投 | `reinvested`，`是` / `否`；只有股息明细里才有 |
| 来源 | 这一条是「股息明细」还是「流水录入」 |

**「只有股息明细里才有」的那几列，在纯流水录入的行里是空格子，不是 `0`。** 「没有明细可查」与「这笔没收过税」对你是两件事 —— 写 `0` 等于替你在文件里宣布了一件没人知道的事。同一条口径也写在 `apps/analytics/dividend_income.py` 的 `DividendEntry` 上。

这张表同样不是手抄完就算：`apps/core/tests/test_export_contract.py` 把它与 `apps/analytics/dividend_export.py` 的 `DIVIDEND_CSV_COLUMNS` 双向对齐（与流水那张表走同一个解析器、同一条判据）。

BOM / CRLF / 公式注入防护 / 文件名给两份这几件事，两个导出**共用同一层实现**（`apps/core/csv_export.py`），行为逐字一致。有一条检查盯着「全仓只有一个渲染出口」：`render_line` / `sanitize_cell` / `quote_cell` 只许在那一个文件里定义，`BOM` / `EOL` 两个字面量也只许出现在那里，两个导出模块的同名函数必须**真的转调**它。各写一份的后果不是重复几十行，而是一个导出带 BOM、另一个不带 —— 你在同一个 Excel 里双击两个文件，一个中文正常、一个乱码。

### 股息日历

`GET /api/v1/analytics/calendar/` 把股息排成「哪一天收到哪笔钱」。`?year=2026` 与 `/analytics/dividends/`、股息导出**同口径**（按派息日筛；`year` 写错回 400）。

数据来源同样只能是归集函数（`services.dividend_entries`）：走 `/transactions/records/` 且 `side=DIVIDEND` 的那条录入路径**只落一条流水、没有明细**，照「把明细表列出来」写，这些股息在日历里**一条都不出现** —— 而同一个页面上的「累计股息」是把它算进去的。上一轮堵掉的是导出那个口子，日历这个口子这一轮才堵上。现在三个出口（图表 / 导出 / 日历）走的是同一个归集函数，并且有一组**读源码**的检查盯着这件事：`DividendRecord` 只许出现在 `services.py` 的两个摘数据函数里，视图层与纯计算层碰它就红（`apps/analytics/tests/test_dividend_exits.py`）；冒烟脚本每次还会真的起一遍服务端，验「只落流水的那笔在日历里」与「日历合计 == 总览的累计股息」。

返回三组 + 合计，一条都不许丢：

| 组 | 判据 | 排序 |
| --- | --- | --- |
| `upcoming` | 派息日在今天**之后** —— DESIGN §4.5 说的「派息日提醒」就是它 | 日期升序（最近的一笔在最前） |
| `received` | 派息日在今天**当天或之前**（今天派的算已到账） | 倒序（刚收到的在最前） |
| `undated` | **派息日为空**的条目 | 按标的、账户、日期 |

派息日未知的那些既不属于待派也不属于已到账，**但必须报出来** —— 悄悄丢掉等于告诉你这笔股息不存在。分组的边界就两条（`pay_date == 今天` 算已到账、`明天` 才进待派），「今天」是**参数**不是模块里读的时钟：读时钟的话这几条边界根本没法断言。

`totals`：

| 字段 | 含义 |
| --- | --- |
| `count` | 条目总数（等于三组条数之和） |
| `by_currency` | 全部条目的分币种合计 |
| `upcoming_by_currency` / `received_by_currency` / `undated_by_currency` | 三个分项各自的分币种合计 |
| `upcoming_count` | 待派的笔数 |
| `next_pay_date` | 下一次派息日；没有待派时是 `null`（不拿「日期未知」的来凑） |

**三个分项逐币种相加等于 `by_currency`**：少一整组时账面上会缺一块，而缺的那块最容易是「日期未知」那组。分币种而不是加总 —— CNY 与 USD 加在一起是个没有意义的数；折算过的那个合计在 `summary.dividend_total` 里，日历的 `by_currency` 加总与它相等。

每条的样子：

| 字段 | 含义 |
| --- | --- |
| `asset_id` / `account_id` | 外键 id |
| `symbol` / `asset_name` / `account_name` | 名字由视图补；查不到时空串（不是缺字段） |
| `currency` / `amount` | 币种与**到手**金额（有明细取 `net`，纯流水取流水金额的绝对值 —— 与「累计股息」同一个数） |
| `pay_date` / `ex_date` | 日期；没有明细的条目 `ex_date` 是 `null` |
| `days_until` | 距派息日还有几天：未到是正数、当天 `0`、已过是负数；日期未知 `null` |
| `origin` | `record`（股息明细）或 `transaction`（流水录入） |
| `reinvested` / `record_id` | 明细字段；纯流水录入的是 `null`，**不是 `0` / `false`** |
| `transaction_id` | 关联的那条流水 id |

金额一律经 `apps/core/csv_export.fmt_decimal` —— **与导出 CSV 同一个函数**。库里是 `DECIMAL(24,8)`，流水录入的股息取出来是 `77.25000000`，直接 `str()` 写进 JSON 就是这个样子，而同一个数在导出文件里是 `77.25`：同一笔股息在两个出口里读起来是两个数，只会让人以为其中一个错了。（这条是冒烟脚本跑出来的：导出那条断言过、日历那条红。）

这一版换掉了旧的 `{"results": [...]}` 形状：那形状里的 `id` 是 `DividendRecord` 的主键，而纯流水录入的股息**没有**这个 id，留着它等于继续暗示「日历里的东西都是明细」。两个客户端目前都还没消费这个接口（`harmony/` 里没有 calendar 的调用）。

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
GET  /api/v1/auth/me/                              当前用户（需认证）
GET  /api/v1/health/                               版本自证（源码指纹，无需认证）
GET/POST /api/v1/accounts/                         账户
GET/POST /api/v1/assets/                           标的
GET/POST /api/v1/transactions/records/             流水（支持 client_request_id 幂等、from/to 日期区间）
GET  /api/v1/transactions/records/export/          流水导出 CSV（筛选条件与列表一致）
POST /api/v1/transactions/records/import/          流水导入 CSV（列名与导出同源，自己导的必然导得回来）
GET/POST /api/v1/transactions/dividends/           股息
GET  /api/v1/market/quotes/?asset_ids=1,2&refresh=1 行情
GET  /api/v1/market/fx/?base=USD&quote=CNY         汇率
POST /api/v1/ingest/image|text                     凭证上传与识别
GET  /api/v1/ingest/jobs/{id}/                     单条识别任务的结果（收件箱轮询）
GET  /api/v1/ingest/drafts/                        草稿箱
POST /api/v1/ingest/drafts/{id}/confirm|discard    确认入账 / 丢弃
GET  /api/v1/analytics/positions|summary|dividends
GET  /api/v1/analytics/calendar/                   股息日历（三组 + 合计，支持 ?year=）
GET  /api/v1/analytics/dividends/export/           股息导出 CSV（与「累计股息」同口径，支持 ?year=）
```

清单不是手抄完就算：`apps/core/tests/test_api_surface_contract.py` 把它与 `config/urls.py`
加各 app 的 `urls.py` **双向**对齐 —— 漏一条、多一条都红。同一个检查还钉住
`apps/*/urls.py` 有没有被挂到根 urlconf：一个 app 有 urls.py 却没被 `include`，它的接口
**存在但访问不到**，全程不报错，只有 404。

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
- [x] M3 股息模块：股息记录、月度聚合、日历（日历是从**已录入的股息**排出来的，见「股息日历」）
- [x] M4 Agent 链路：文本与截图识别 → 草稿箱 → 确认入账
- [ ] M5 打磨：截图上传端上接入、图表、导出与导入 CSV（**流水与股息都已支持**）、通知、真机签名

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
- 股息数据以手动录入与 Agent 识别为主。日历（`analytics/calendar/`）拼的是**已录入的股息**，「抓公告自动生成未来的派息日」仍在 TODO
- 客户端默认后端地址（`10.0.2.2:8000`）**现在只有一处定义**：`common/ApiClient.ets` 里的
  `DEFAULT_API_BASE_URL`，`EntryAbility.ets` import 它（原先两处各写一遍）。本机没有
  DevEco / hvigor 工具链，编译不出来，所以改这个地址时只有两条自测兜着：
  `test_api_surface_contract.py` 钉住「README 运行环境表里的模拟器地址 = 那一处声明」，
  并且 `EntryAbility` 里**不许再长出第二个 URL 字面量**、且必须真的把常量写进
  `AppStorage`（「提到名字」不算用了它 —— 这是负向验证教出来的）。改地址记得在 DevEco 里过一遍编译
- 承接上一条：本机没有鸿蒙工具链，`.ets` 的正确性**只**能靠读源码的契约检查兜底。目前
  钉住的是三面 —— 「客户端页面清单 / import 落点 / 路由登记」（`test_client_pages_contract.py`，
  其中 import 那一面覆盖具名 / 默认 / 命名空间 / 只为副作用**四种写法**，并且额外拿一个宽扫
  给**解析面自己**对账 —— 出现第五种写法会直接红，而不是静默漏过）、
  「默认后端地址只有一处且被真的用上」（`test_api_surface_contract.py`）、
  以及「客户端打的每个接口路径都真实存在」（`test_client_route_contract.py`，
  同样是宽窄两遍逐文件对账）——**这三面没覆盖到的 `.ets` 改动，自测一律看不见**。
  加 Tab、加页面、改 import、加接口调用时，记得把新的不变式也一并钉住，
  否则下次就轮到它静默漂移
- `/api/v1/health/` 的指纹在入哈希前把行尾统一成 LF，所以**「只改了行尾」不算改动** ——
  这是刻意的（同一个提交在 Windows 与 Linux 上必须是同一个指纹），代价是它答不了
  「行尾有没有被改坏」这类问题
