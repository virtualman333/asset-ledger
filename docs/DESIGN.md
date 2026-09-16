# 投资资产记录 App — 方案设计 v1

> 定位：一个**以股息和现金流为核心视角**的多市场资产账本。手动录入保底，Agent 截图/文本自动识别提效，后端统一抓价、算收益、算股息。
> 一句话原则：**AI 只生成草稿，人确认才入账；原凭证永久留存可溯源。**

---

## 1. 环境勘察（本机现状）

| 项 | 现状 | 影响 |
| --- | --- | --- |
| 鸿蒙工具链 | **完全为空**，无 DevEco Studio、无 SDK、无 ohpm/hvigor、无 JDK | 需先下载 DevEco Studio 5.x + HarmonyOS SDK（约 4-6GB），M0 前必须解决 |
| Python | 3.13.14（托管）/ 3.14.3（系统），**Django 未安装** | 用托管 3.13 + venv，Django 5.2 LTS |
| MySQL | 目录在 `C:\Program Files\MySQL\MySQL Server 8.4`，**服务未运行/未注册** | 需 `net start MySQL84` 核实，或重装服务；否则退回 WSL/Docker |
| GitHub CLI | **未安装**；凭据管理器里有 `git:https://github.com` 凭证 | winget 装 `GitHub.cli` 或网页建仓；push 走 HTTPS 凭证 |
| 磁盘 | C 盘 469GB 可用，D 盘 1.2TB 可用 | 充足，SDK 建议装 D 盘 |
| git 身份 | `yongguichen / yongguichen@tencent.com` | 需确认是否用 `virtualman333` 账号建仓 |

---

## 2. 技术选型

### 2.1 客户端（原生鸿蒙）

| 维度 | 选型 | 说明 |
| --- | --- | --- |
| 语言/框架 | ArkTS + ArkUI 声明式，Stage 模型 | HarmonyOS NEXT（API 12+） |
| 网络 | `@kit.NetworkKit`（http / rcp） | 统一封装拦截器：JWT 注入、刷新、错误码 |
| 本地存储 | `relationalStore`（SQLite）+ `Preferences` | 离线优先：先看本地，再拉增量 |
| 图表 | `@ohos/mpchart`（三方 ohpm） | 后续复杂图表再自绘 Canvas |
| 凭证安全 | `@ohos.security.asset` 或 HUKS | Token 不落 plaintext |
| 主题 | 品牌紫 `#7166F0`，亮/暗双主题 | 与已有项目调性一致 |
| 系统入口 | 分享 Want / 智慧识屏 → 本 App | 截完图直接分享进来，是最高频路径 |

**避坑（已确认为硬伤）**：HarmonyOS NEXT **默认禁止明文 HTTP**。开发期必须在 `module.json5` 配 `networkSecurityConfig` 放行 cleartext（仅 debug 包），正式环境一律 HTTPS。

### 2.2 后端

| 维度 | 选型 | 说明 |
| --- | --- | --- |
| 框架 | Django 5.2 LTS + DRF + SimpleJWT | Python 3.13（托管版 venv） |
| 数据库 | MySQL 8.4 | 驱动优先 `mysqlclient`，Windows 装不上退回 `PyMySQL` |
| 任务调度 | **开发期：Django-Q2 / APScheduler 单进程**；生产 Linux：Celery + Redis | Windows 上 Celery worker 坑多，不建议主力 |
| 部署 | 开发：本机 `0.0.0.0:8000`；长期：云服务器 + Nginx + Gunicorn + HTTPS | 模拟器用 `10.0.2.2:8000`，真机用局域网 IP 或公网域名 |
| 规范 | ruff + pytest + drf-spectacular（OpenAPI） | 接口文档自动生成，端上可对照 |

---

## 3. 数据模型（核心表）

| 表 | 作用 | 关键字段 |
| --- | --- | --- |
| `User` | 用户 | Django 内置 |
| `Account` | 账户/券商 | name, broker, market, currency, type |
| `Asset` | 标的（资产主数据） | symbol, name, market(A/US/HK/FX/CRYPTO/FUND), currency, type(STOCK/ETF/BOND/CASH/CRYPTO/FOREX), is_dividend_asset |
| `Transaction` | 流水（唯一事实源） | account, asset, side(BUY/SELL/DIVIDEND/INTEREST/DEPOSIT/WITHDRAW/FEE/TAX/SPLIT), quantity, price, amount, fee, tax, currency, fx_rate, traded_at, source(MANUAL/AGENT/IMPORT), evidence_id |
| `Position` | 持仓快照（可由流水推导，物化提速） | account, asset, quantity, avg_cost, currency, updated_at |
| `DividendRecord` | 股息明细（挂在流水之上） | tx, asset, ex_date, pay_date, amount_per_share, shares, gross, tax, net, currency, reinvested(bool) |
| `PriceQuote` | 行情快照 | asset, price, currency, change_pct, source, fetched_at（联合唯一 + 时间索引） |
| `FxRate` | 汇率 | base, quote, rate, date |
| `IngestJob` | Agent 抽取任务 | user, kind(IMAGE/TEXT), raw_file, raw_text, status, result_json, confidence, client_request_id(唯一，幂等) |
| `Evidence` | 原始凭证 | file, ocr_text, sha256, uploaded_at（永不硬删，仅解绑） |

设计要点：
- **流水是唯一事实源**，持仓与所有统计一律从流水推导（可物化缓存），避免多处对不上账。
- `IngestJob.client_request_id` 唯一约束 → 网络重试不会重复入账。
- 所有金额字段用 `Decimal`，币种显式，入账时锁定 `fx_rate`。

---

## 4. 功能模块

### 4.1 手动记录（M1）
- 交易录入：买入/卖出/分红/利息/出入金/费用/税/拆股，支持多账户、多币种、自动带出汇率
- 批量导入：CSV（券商流水导出），字段映射可保存为模板
- 持仓与流水列表：筛选、搜索、按月分组

### 4.2 Agent 自动记录（M4，差异化核心）
- **输入通道**：① 截图（券商成交页 / 持仓页 / 分红通知 / 钱包转账）② 复制文本（公告、消息、邮件片段）③ 系统分享直接投进 App
- **抽取**：多模态 LLM → 强制 JSON Schema 输出
  ```json
  {"side":"BUY","symbol":"600519","market":"A","name":"贵州茅台",
   "quantity":100,"price":1680.5,"fee":5.0,"tax":0,
   "currency":"CNY","traded_at":"2026-09-15T10:32:00+08:00",
   "account_hint":"华泰","confidence":0.93,"missing":[]}
  ```
- **硬约束**：结果永远进**草稿箱**，绝不直接写流水；`confidence < 0.8` 或 `missing` 非空 → 字段标红、强制人工补全
- **溯源**：草稿卡片并排显示"原图 + 识别结果"，点击字段跳转到原图对应位置（存 bbox）；入账后 `Transaction.evidence_id` 永久关联
- **纠错闭环**：用户改错 → 记录 diff → 作为 few-shot 样本回喂，越用越准

### 4.3 实时价格（M2）

| 市场 | 数据源 | Key | 频率建议 |
| --- | --- | --- | --- |
| A 股 / 港股 | 腾讯 `qt.gtimg.cn`、东财 push2 | 免费 | 盘中 10-30s |
| 美股 | Yahoo `query1.finance.yahoo.com` / Stooq | 免费（15min 延迟） | 5min |
| 基金/ETF | 天天基金估值接口 | 免费 | 盘中 1min |
| 外汇 | Frankfurter（ECB）/ exchangerate-api | 免费 | 日更 |
| 加密 | **OKX 公开 API** / CoinGecko | 免费 | 实时 |
| 股息日历 | A 股东财 F10、美股 Yahoo/dividendhistory | 免费/半自动 | 日更 |

统一 `PriceSource` 抽象：`fetch(asset) -> Quote`，加限流、失败退避、缓存；任何一家挂了可热切换。端上只向后端要价，不直连第三方（避免跨域与 Key 泄露）。

### 4.4 收益统计（M2-M3）

| 指标 | 口径 |
| --- | --- |
| 持仓成本 | 移动加权平均（可选 FIFO/LIFO，默认加权） |
| 浮动盈亏 | 市值 − 成本 |
| 已实现盈亏 | 卖出所得 − 对应成本 − 费 − 税 |
| 总收益 | 浮盈 + 已实现 + 股息/利息 − 费用 |
| **年化** | **XIRR（现金流法）**，把出入金当现金流，比"总收益/本金"准确 |
| 多币种 | 统一折算基准货币（CNY 或 USD），显示"含汇率影响/不含汇率影响"两栏 |
| 股息 | 税前/税后/净额、累计股息、股息率（年度股息 ÷ 持仓成本）、持仓层面的"预期年股息"、分红再投（DRIP）可选 |
| 视图 | 总资产走势、各市场占比、个股盈亏排行、月度股息柱状图 + 同比 |

### 4.4.1 流水的现金变动（`amount`）

`amount` 是账户现金变动，**正数 = 资金流入、负数 = 资金流出**（已含 `fee` / `tax`）。
推导与符号归一只有一处：`server/apps/transactions/amount_rules.py`。

| side | 没给 `amount` | 给了 `amount` |
| --- | --- | --- |
| `BUY` | `−(数量×单价 + fee + tax)` | 原样 |
| `SELL` / `DIVIDEND` / `FEE` / `TAX` | `数量×单价 − fee − tax` | 原样 |
| `SPLIT` | `0` | 原样 |
| `DEPOSIT` / `WITHDRAW` | 回 400 | 按 side 归一符号；为 0 也回 400 |

三条必须写下来的理由：

1. **出入金不能「推」出来。** 它们没有数量×单价。在推导分支里与买卖共用一条兜底路径，
   结果就是一笔入金的 `amount` 变成 `0` —— 而 XIRR 正是靠出入金定现金流的端点，
   金额为 0 等于这笔钱从未投入，年化算出来的数字与实际无关，**全程不报错**。
   所以现在缺金额直接回 400，让用户补。
2. **拆股不产生现金变动。** 它同样不该走「数量×单价」那条分支：录入时只要顺带填了
   单价，就会凭空多出一笔正的现金流入，总资产随之虚增。
3. **方向由 `side` 决定，金额只表达大小。** 用户按直觉把「出金 50000」填成正数，
   与「正数=流入」的约定相反；XIRR 用 `−amount` 定方向，出金于是被当成入金。
   出入金这两档一律按 side 归一符号。

Agent 识别链路（`apps/ingest/`）的草稿 schema 里包含 `amount`，确认入账时透传 ——
否则识别出方向却没有金额，又是一笔 0。

### 4.5 股息专项（M3）
股息日历（除权日/派息日提醒）、单标的股息历史与走势、持仓组合"月度被动收入"预测、股息再投入自动记一笔买入。

---

## 5. 工程结构

```
asset-ledger/                 # monorepo（建议）
├── harmony/                  # 鸿蒙工程（DevEco 打开这一层）
│   ├── entry/                # 主模块（NEXT 单 hap）
│   │   └── src/main/ets/
│   │       ├── common/       # 常量、主题、工具、http 封装
│   │       ├── model/        # 数据模型
│   │       ├── data/         # repository + local(RDB) + remote
│   │       └── pages/        # Holdings / Transactions / Dividends / Inbox / Stats / Settings
│   ├── hvigor/  oh-package.json5
│   └── .gitignore            # oh_modules, build, .hvigor
├── server/                   # Django
│   ├── config/               # settings(split: base/dev/prod), urls, celery
│   ├── apps/
│   │   ├── accounts/  assets/  transactions/  positions/
│   │   ├── market/           # 价格源适配 + 调度
│   │   ├── ingest/           # Agent 抽取（LLM 客户端 + schema + 幂等）
│   │   └── analytics/        # 成本、盈亏、XIRR、股息
│   ├── requirements.txt  manage.py
│   └── .gitignore            # venv, __pycache__, .env, db.sqlite3
├── docs/                     # 本文件 + API.md + 数据字典
└── scripts/                  # 建库、初始化、一键启动
```

## 6. API 草案（`/api/v1/`）

```
POST   /auth/register  /auth/token  /auth/token/refresh
GET    /accounts/                     CRUD
GET    /assets/?q=                    搜索/创建标的
GET    /transactions/?account=&asset=&from=&to=   分页
POST   /transactions/                 手动新增（幂等 key）
PATCH/DELETE /transactions/{id}/
POST   /ingest/image                  上传截图 → 返回 job_id
POST   /ingest/text                   提交文本 → 返回 job_id
GET    /ingest/jobs/{id}              轮询抽取结果
GET    /ingest/drafts/                草稿箱
POST   /ingest/drafts/{id}/confirm    确认入账（可先改字段）
POST   /ingest/drafts/{id}/discard
GET    /positions/                    持仓 + 实时价 + 浮盈
GET    /quotes/?symbols=              批量行情
GET    /analytics/summary             总览（资产、收益、年化）
GET    /analytics/dividends?year=     股息明细与月度聚合
GET    /analytics/calendar            股息日历
```

## 7. GitHub 仓库规划

- 单仓 monorepo（一个人开发联调最省事），名字候选：`asset-ledger` / `dividend-book` / `holdings-hub`
- 分支：`main`（保护）+ 短生命周期 `feat/*`，直接 PR 合并；不搞 develop 长分支
- CI（GitHub Actions）：server 侧 `ruff + pytest`；harmony 侧先只做 `oh-package` 版本检查（Linux 跑 hvigor 成本高，暂不构建）
- Secret：`DJANGO_SECRET_KEY`、LLM API Key 走 Actions Secrets，本地 `.env` 且 gitignore
- README：架构图 + 本地启动三行命令 + 截图位

## 8. 里程碑

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| M0 | 装 DevEco + SDK；GitHub 建仓；Django 骨架 + MySQL 连通；鸿蒙 hello + 打通一次真接口 | 两端能握手 |
| M1 | 标的、账户、流水 CRUD + 列表；本地缓存 | 手动记账闭环可用 |
| M2 | 行情调度 + 持仓/收益/资产走势 | 能看到"赚了多少" |
| M3 | 股息模块：记录、日历、月度被动收入、XIRR | 核心差异化成立 |
| M4 | Agent 抽取：截图 + 文本 → 草稿箱 → 入账 + 溯源 | 提效闭环 |
| M5 | 打磨：暗色、图表细化、导出 CSV、通知、签名与真机调试 | 可日常使用 |

## 9. 风险与取舍

| 风险 | 应对 |
| --- | --- |
| 鸿蒙工具链从零开始，下载体积大 | 先只装 SDK + 模拟器；真机签名（AGC）可延后到 M5 |
| 无 HarmonyOS NEXT 真机 | 模拟器先跑通；真机调试必须有华为账号 + 调试签名 |
| LLM 识别错数字 = 账目错 | 草稿箱确认 + 低置信标红 + 原图对照 + 可回滚，账目安全不依赖模型准确率 |
| 行情源不稳定/被限流 | 多源热切换 + 缓存兜底（用上一次价格并标记"延迟"） |
| XIRR 在极端现金流下不收敛 | 加二分兜底，失败时降级为简单年化并标注 |
| Windows 上 Celery 难搞 | 开发期 Django-Q2；生产放 Linux |

## 10. 需要拍板（定了我就能开工）

1. **仓库归属与命名**：`virtualman333` 还是 `yongguichen`？名字用 `asset-ledger` 吗？monorepo 单仓可以吗？
2. **鸿蒙调试设备**：有 HarmonyOS NEXT 真机吗（决定 M0 是模拟器还是真机签名）？
3. **LLM 供应商**：OpenAI/兼容接口、通义千问 VL、还是你有现成的 API Key（决定 M4 实现；没有就先做规则+正则兜底）？
4. **后端跑在哪**：先本机局域网（手机连 WiFi 访问）还是直接上云服务器（决定要不要提前搞 HTTPS）？
5. **优先级**：是先做能用的手动账本（M1-M3 优先），还是要我先搭出 Agent 链路跑通一个 demo？
