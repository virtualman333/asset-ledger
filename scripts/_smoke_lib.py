# -*- coding: utf-8 -*-
"""两个冒烟脚本的公共底座：报告器 + 目标服务端的解析 + 「自启一个只服务当前代码的服务端」。

要解决的问题
------------
`smoke_api.py` 与 `smoke_record_flow.py` 原先各自写死
`BASE = "http://127.0.0.1:8000/api/v1"`。写死一个地址，等于把「验证这份代码」这句承诺
转包给了「8000 端口上那个进程恰好是这份代码」这个约定 —— 而脚本对它一无所知：它是谁起的、
什么时候起的、跑的是哪个提交，全都不知道。

实测撞上过：端口上挂着一个两天前起的旧进程，它的 `/analytics/summary/` 还是旧字段
（没有 `dividend_yield` / `monthly_passive_income` / `dividend_annual`），于是 26 条检查
里 5 条 FAIL —— 全是**假缺陷**；随后脚本在 `summary["dividend_yield"]` 上 KeyError 中断，
**连失败小结都没打印出来**。一次误诊，加一次「没有结论」。两件事都由那一行地址造成：

- 地址写死 → 脚本能回答的问题只能是「那个进程对不对」，而不是「我这份代码对不对」；
- 断言直接下标取值 → 响应形状一变就不是「失败」，而是「崩溃」。

所以这个底座提供三件事
----------------------
1. `Report`：检查记录 + **异常兜底**。`run()` 把 body 抛出的任何异常折算成一条 FAIL，
   退出码永远由检查结果决定 —— 「跑出一半没有结论」这种结局不存在。
2. `resolve_target()`：给了 `--base` / `AL_SMOKE_BASE` 就连过去（并在开头声明它无法自证），
   没给就**自启**。没有「默认连 8000」这一档。
3. `start_server()`：拿当前 checkout 起一个 `runserver --noreload`，跑完杀掉。于是
   「测的就是这份代码」由构造保证，不靠约定。

只用标准库
----------
`server/apps/core/tests/test_smoke_lib.py` 会 import 本模块，而按 README 的承诺，那套单测
**不需要 Django、也不需要第三方依赖** —— 所以这里不许 import `requests`，探活用 urllib 就够。
"""
from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from pathlib import Path

#: 仓库根（本文件在 `scripts/` 下）
ROOT = Path(__file__).resolve().parents[1]

#: 所有接口都挂在 `/api/v1` 下；`--base` 允许只给到 `host:port`
API_SUFFIX = "/api/v1"

#: `--base` 的等价环境变量
ENV_BASE = "AL_SMOKE_BASE"

#: 自启时最多等多久。本机 runserver 通常半秒内起来，留这么宽是为了「第一次跑」和冷启动。
READY_TIMEOUT = 40.0


def api_base(url: str) -> str:
    """把用户给的地址规整成 `<host>/api/v1`：去掉尾部斜杠，缺 `/api/v1` 时补上。

    幂等 —— 已经带 `/api/v1` 的再传一次不会变成 `/api/v1/api/v1`。
    """
    url = (url or "").strip().rstrip("/")
    if not url:
        return ""
    if not url.endswith(API_SUFFIX):
        url += API_SUFFIX
    return url


class Report:
    """冒烟检查的记录与结论。"""

    def __init__(self) -> None:
        self.total = 0
        self.fails: list[str] = []

    def check(self, name: str, ok: bool, detail: object = "") -> bool:
        """记一条检查，返回结论本身（方便 `if check(...):` 这种用法）。"""
        ok = bool(ok)
        self.total += 1
        print(f"{'PASS ' if ok else 'FAIL '} {name}{'' if ok else '  -> ' + str(detail)[:200]}")
        if not ok:
            self.fails.append(name)
        return ok

    def skip(self, name: str, why: str = "") -> None:
        """记一条「没跑」。它不算失败，但也不能不声不响 —— 报告里得看得见。"""
        print(f"SKIP   {name}{('  -> ' + why) if why else ''}")

    def field(self, data: object, key: str, default: object = None) -> object:
        """读响应里的**业务字段**。

        **不要用 `data[key]`**：响应形状一变，下标取值就是 KeyError，脚本会在「没有结论」
        的状态下结束。「少了一个字段」本身应该由一条 `check(...)` 报出来，detail 里带上
        实际收到的字段名 —— 那才是有用的失败。

        管道类 id（token / account / asset）例外，直接下标即可：缺了它后面每一步都无从
        谈起，让兜底把整条链路判为失败反而更清楚。
        """
        return data.get(key, default) if isinstance(data, dict) else default

    def finish(self) -> int:
        """打印小结并给出退出码：有失败就是 1，一条都没有才是 0。"""
        print()
        if self.fails:
            print(f"结果：{len(self.fails)}/{self.total} 条失败")
            print("失败项：" + "、".join(self.fails))
            return 1
        print(f"结果：全部通过（{self.total} 条）")
        return 0


def _parser(prog: str | None = None) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=prog)
    parser.add_argument(
        "--base",
        default=os.getenv(ENV_BASE, ""),
        metavar="URL",
        help=(
            "要验的服务端地址（缺 /api/v1 会自动补），也可以用环境变量 "
            f"{ENV_BASE}。不给就自启一个只服务当前代码的服务端。"
        ),
    )
    return parser


def resolve_target(argv: list[str] | None = None, prog: str | None = None) -> tuple[str, str]:
    """解析出 `("external", base)` 或 `("self", "")`。

    规则只有一条：**给了地址就连过去，没给就自启**。
    没有「默认连 127.0.0.1:8000」这一档 —— 那正是会让脚本误诊的那一档。
    """
    known = _parser(prog).parse_args(argv)
    base = api_base(known.base)
    return ("external", base) if base else ("self", "")


def free_port() -> int:
    """问内核要一个当下可用的端口（绑 0 让内核分配，读完立刻释放）。"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _tail(path: Path, lines: int = 25) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:  # pragma: no cover - 只在日志文件被别的东西删掉时走到
        return f"（读不到日志 {path}：{exc}）"
    return "\n".join(text[-lines:]) or "（日志是空的）"


def wait_ready(
    base: str, proc: subprocess.Popen, log_path: Path, timeout: float = READY_TIMEOUT
) -> None:
    """等 runserver 应答。

    先看进程死没死：起不来（没装 Django、连不上库、端口被占）时立刻把日志尾巴端出来，
    比干等 40 秒再报「超时」有用得多 —— 那条信息里就有原因。
    被 DRF 用 401 拒掉、或路由给 405，都算「已就绪」：那证明路由挂上了、DRF 在应答。
    """
    deadline = time.monotonic() + timeout
    while True:
        if proc.poll() is not None:
            raise RuntimeError(
                f"服务端启动即退出（退出码 {proc.returncode}），日志尾巴：\n{_tail(log_path)}"
            )
        try:
            urllib.request.urlopen(f"{base}/accounts/", timeout=2)  # noqa: S310 - 只连本机
            return
        except urllib.error.HTTPError as exc:
            if exc.code in (400, 401, 403, 405):
                return
        except Exception:  # noqa: BLE001 - 还没起来时的连接错误有十几种，一律重试
            pass
        if time.monotonic() > deadline:
            raise RuntimeError(
                f"等了 {timeout:.0f}s 服务端仍不应答，日志尾巴：\n{_tail(log_path)}"
            )
        time.sleep(0.3)


@contextlib.contextmanager
def start_server(port: int | None = None, timeout: float = READY_TIMEOUT):
    """拿**当前 checkout** 起一个服务端，yield `(base, log_path)`，退出时杀掉。

    `--noreload` 不是可选项：runserver 默认会 fork 一个 reloader 父进程，`terminate()`
    只杀得掉父进程，子进程会继续占着端口 —— 每跑一次冒烟就漏一个进程。
    """
    port = port or free_port()
    base = f"http://127.0.0.1:{port}{API_SUFFIX}"
    log_path = Path(tempfile.gettempdir()) / f"asset_ledger_smoke_{port}.log"
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(  # noqa: S603 - argv 全是常量，不含用户输入
            [sys.executable, "manage.py", "runserver", f"127.0.0.1:{port}", "--noreload"],
            cwd=str(ROOT / "server"),
            stdout=log,
            stderr=subprocess.STDOUT,
        )
    try:
        wait_ready(base, proc, log_path, timeout)
        yield base, log_path
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover - 正常路径不会走到
            proc.kill()


def run(body, argv: list[str] | None = None, prog: str | None = None) -> int:
    """跑一个冒烟脚本并**无条件给出结论**，返回退出码。

    `body(base, report)` 抛异常时不再让 traceback 直接冒出去：那样脚本会在「没有结论」
    的状态下结束（原 `smoke_record_flow.py` 就是这样 —— 5 条 FAIL 之后 KeyError，
    失败小结没打印，退出码也不再反映检查结果）。异常一律折算成一条 FAIL。
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    prog = prog or Path(sys.argv[0]).name
    mode, base = resolve_target(argv, prog)
    report = Report()
    print(f"== 冒烟：{prog} ==")
    try:
        if mode == "external":
            print(f"服务端：--base 指定的外部服务  {base}")
            print(
                "   ⚠ 本脚本无法证明它跑的是当前这份代码 —— 断言成片失败时，"
                "先重启服务端再重跑（或者不带 --base 跑，让它自己起一个）。"
            )
            body(base, report)
        else:
            with start_server() as (base, log_path):
                print(f"服务端：自启（当前 checkout）  {base}")
                print(f"   （跑完随脚本一起关闭；启动日志 {log_path}）")
                body(base, report)
    except Exception as exc:  # noqa: BLE001 - 故意兜住一切：「崩溃」不是允许的结局
        traceback.print_exc()
        report.check(f"冒烟流程未跑完（{type(exc).__name__}）", False, str(exc)[:200])
    return report.finish()
