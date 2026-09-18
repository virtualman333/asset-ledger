# -*- coding: utf-8 -*-
"""客户端 `.ets` 的**文件清单与默认地址** —— 两份客户端契约共用的底座。

为什么单独一个模块
------------------
`harmony/entry/src/main/ets/` 下那份文件清单、以及「默认后端地址声明在哪一行」这两件事，
现在有两份契约要读：

  - `test_api_surface_contract.py`：README 说的模拟器地址 ↔ 客户端默认地址；
  - `test_client_route_contract.py`：拿客户端打的路径去比服务端路由，而路径是**相对基址**的
    （`ApiClient.send()` 拼的是 `${getBaseUrl()}${path}`），所以它必须知道基址以什么结尾。

一份事实两份读法，就把读法本身收敛到这里 —— 否则哪天地址改名，两份检查里总有一份
还在读旧写法，而它会以「什么都没找到、所以全绿」的方式安静下去。

`.ets` 不是 Python，AST 用不上，这里只能按文本读；所以这两个提取器都配了「条数自证」，
调用方要断言「恰好一条」而不是「至少一条」。
"""
import re
from pathlib import Path

from apps.core.route_inventory import ROOT

#: 客户端源码根
ETS_ROOT = ROOT / "harmony/entry/src/main/ets"

#: 默认后端地址的声明行：`export const DEFAULT_API_BASE_URL: string = 'http://...';`
#: 只认**声明**，不认引用 —— 收敛之后这个字面量全客户端只该出现这一次。
BASE_URL_DECL_RE = re.compile(
    r"DEFAULT_API_BASE_URL\s*:\s*string\s*=\s*'([^']+)'", re.M
)


def client_sources():
    """全部 `.ets` 文件 —— 返回 `(相对仓库根的路径, 文本)`，按路径排序。"""
    return [
        (path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8"))
        for path in sorted(ETS_ROOT.rglob("*.ets"))
    ]


def declared_base_url():
    """客户端声明的默认后端地址。

    找不到或找到多份都**抛异常**：返回 `None` 或随便挑一条，都会让调用方的断言
    从「地址对不对」退化成「有没有」—— 而后者在地址被删掉时是绿的。
    """
    found = []
    for rel, text in client_sources():
        for value in BASE_URL_DECL_RE.findall(text):
            found.append((rel, value))
    if len(found) != 1:
        raise AssertionError(
            f"客户端里 DEFAULT_API_BASE_URL 的声明不是恰好一处：{found!r}\n"
            "要么没声明，要么又被人抄了第二份 —— 两种都会让「只有一个默认地址」变成假话。"
        )
    return found[0][1]
