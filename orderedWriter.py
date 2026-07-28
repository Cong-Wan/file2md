'''
Author: wilbur
Version: 1.0
  Date: 2026-04-24
  Description: 按页序线程安全 append 到 Markdown 文件的写入器，
               支持乱序 submit、失败占位、断点续传 startPage 偏移
'''

import os
import threading


# ============================================================
# 日志工具
# ============================================================

def _log(msg: str, level: str = "INFO", verbose: bool = True):
    if not verbose and level == "DEBUG":
        return
    from datetime import datetime
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    prefix = {
        "INFO":  "\033[32m[INFO ]\033[0m",
        "DEBUG": "\033[36m[DEBUG]\033[0m",
        "WARN":  "\033[33m[WARN ]\033[0m",
        "ERROR": "\033[31m[ERROR]\033[0m",
    }.get(level, "[????]")
    print(f"{timestamp} {prefix} [OrderedWriter] {msg}", flush=True)


# ============================================================
# OrderedMarkdownWriter
# ============================================================

class OrderedMarkdownWriter:
    """
    Thread-safe writer that appends Markdown content to a file in strict
    page-number order, even if pages are submitted out of order by
    concurrent threads.

    Features:
    - Out-of-order submit with ordered flush
    - Failure placeholders (submitFailed)
    - startPage offset for resume scenarios
    - Durable writes (flush + fsync)
    """

    def __init__(
        self,
        outputPath: str,
        startPage: int = 1,
        separator: str = "\n\n---\n\n",
        failMarker: str = "<!-- [翻译失败，保留原文] -->\n\n",
        verbose: bool = False,
    ):
        self._lock = threading.Lock()
        self._ready: dict[int, str] = {}          # pages waiting to be flushed
        self._nextPage = startPage                  # next page to flush
        self._outputPath = outputPath
        self._separator = separator
        self._failMarker = failMarker
        self._verbose = verbose

        # Determine whether this is the first write (no leading separator needed)
        if os.path.exists(outputPath) and os.path.getsize(outputPath) > 0:
            self._isFirstWrite = False
        else:
            self._isFirstWrite = True

        _log(f"初始化完成: outputPath={outputPath}, startPage={startPage}, "
             f"isFirstWrite={self._isFirstWrite}", verbose=verbose)

    # --------------------------------------------------------
    # Public API
    # --------------------------------------------------------

    def submit(self, pageNum: int, content: str) -> None:
        """Submit a successfully translated page. Thread-safe. May trigger one or more flushes."""
        with self._lock:
            _log(f"submit: pageNum={pageNum}, contentLen={len(content)}", level="DEBUG", verbose=self._verbose)
            self._ready[pageNum] = content
            self._flushReady()

    def submitFailed(self, pageNum: int, rawContent: str) -> None:
        """Translation failed: use raw content + fail marker as placeholder. Still advances nextPage."""
        with self._lock:
            _log(f"submitFailed: pageNum={pageNum}, rawContentLen={len(rawContent)}", level="WARN", verbose=self._verbose)
            self._ready[pageNum] = self._failMarker + rawContent
            self._flushReady()

    def getPendingPages(self) -> list[int]:
        """Diagnostic: return sorted list of page numbers that have been submitted but not yet flushed."""
        with self._lock:
            return sorted(self._ready.keys())

    @property
    def nextPage(self) -> int:
        """Current nextPage pointer (next page to flush)."""
        with self._lock:
            return self._nextPage

    # --------------------------------------------------------
    # Internal
    # --------------------------------------------------------

    def _flushReady(self) -> None:
        """
        MUST be called while holding self._lock.
        While self._nextPage is in self._ready:
          pop it, append to file (with separator if not first write), flush+fsync, advance nextPage.
        """
        while self._nextPage in self._ready:
            pageNum = self._nextPage
            content = self._ready.pop(pageNum)
            _log(f"flushing page {pageNum} (contentLen={len(content)})", level="DEBUG", verbose=self._verbose)

            with open(self._outputPath, "a", encoding="utf-8") as f:
                if not self._isFirstWrite:
                    f.write(self._separator)
                f.write(content)
                f.flush()
                os.fsync(f.fileno())

            self._isFirstWrite = False
            self._nextPage += 1

        _log(f"flush done: nextPage={self._nextPage}, pending={sorted(self._ready.keys())}",
             level="DEBUG", verbose=self._verbose)
