'''
Author: wilbur
Version: 1.0
Date: 2026-07-28
Description: 统一日志工具，供 pipeline 及 core 各模块使用，支持模块 tag 前缀
'''

from datetime import datetime


def log(msg: str, level: str = "INFO", verbose: bool = True, tag: str = ""):
    if not verbose and level == "DEBUG":
        return
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefix = {
        "INFO":  "\033[32m[INFO ]\033[0m",
        "DEBUG": "\033[36m[DEBUG]\033[0m",
        "WARN":  "\033[33m[WARN ]\033[0m",
        "ERROR": "\033[31m[ERROR]\033[0m",
        "STEP":  "\033[35m[STEP ]\033[0m",
        "DONE":  "\033[34m[DONE ]\033[0m",
    }.get(level, "[????]")
    tagStr = f"{tag} " if tag else ""
    print(f"{timestamp} {prefix} {tagStr}{msg}", flush=True)


def logSeparator(title: str = "", verbose: bool = True):
    if not verbose:
        return
    pad = (58 - len(title)) // 2
    if title:
        print(f"\033[35m{'=' * pad} {title} {'=' * (58 - pad - len(title))}\033[0m", flush=True)
    else:
        print(f"\033[35m{'=' * 60}\033[0m", flush=True)
