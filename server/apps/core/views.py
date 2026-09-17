# -*- coding: utf-8 -*-
"""`/api/v1/health/` —— 让调用方核对「你跑的是哪份代码」。

为什么需要它
------------
冒烟脚本有一条 `--base` 路径（验一个已经在跑的服务，比如部署到服务器之后）。那条路上
「验的是这份代码」全靠一个没人检查过的约定撑着，实测因此误诊出 5 条假缺陷
（见 `source_stamp.py` 与 `scripts/_smoke_lib.py` 的说明）。让服务端报出自己的源码指纹，
调用方就能把那个约定变成一次**比对**：相同才是同一份代码，不同就当场说清「你验的不是我」。

**不需要认证**：探活与「部署完验一遍」都发生在拿到 token 之前。它暴露的信息只有
「这确实是 asset-ledger」和源码的一个内容哈希 —— 源码本来就公开在仓库里。
**绝对路径、SECRET_KEY、数据库连接串一律不放**，那是另一类东西。

这一层刻意很薄：判断逻辑全在 `source_stamp.py`（纯标准库、可离线断言），这里只负责
把 `settings.BASE_DIR` 递进去、把结果摆成 JSON。
"""
import os
import time

from django.conf import settings
from django.http import JsonResponse

from . import source_stamp

#: 报告里最多点名几个「进程起来之后又被改过」的文件 —— 通常一个都不该有，
#: 真出现了，头几个就够定位了
MAX_STALE_FILES = 5


def health(request):
    snapshot = source_stamp.scan(settings.BASE_DIR)
    started = source_stamp.PROCESS_STARTED_AT
    changed = snapshot.changed_after(started)
    return JsonResponse(
        {
            "service": "asset-ledger",
            "pid": os.getpid(),
            "started_at": source_stamp.iso(started),
            "uptime_seconds": round(max(0.0, time.time() - started), 3),
            "source": {
                "files": snapshot.files,
                "fingerprint": snapshot.fingerprint,
                "newest_file": snapshot.newest_file or None,
                "newest_mtime": source_stamp.iso(snapshot.newest_mtime) if snapshot.files else None,
                # 下面三条：这一份源码**和这个进程**是不是同步的
                "unreadable": list(snapshot.unreadable),
                "stale": bool(changed),
                "stale_files": changed[:MAX_STALE_FILES],
            },
        }
    )
