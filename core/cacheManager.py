'''
Author: wilbur
Version: 1.4
  Date: 2026-07-28
  Description: 迁移至 core 包，日志改为 core.logUtils 统一实现并补 [CacheManager] tag

Version: 1.3
  Date: 2026-05-21
  Description: 升级缓存为 v3.0，拆分解析、图片理解、翻译状态并支持任务模式恢复

Version: 1.2
  Date: 2026-04-24
  Description: 新增翻译状态跟踪（translationStatus、translatedContent、translatedAt）

Version: 1.1
  Date: 2026-04-23
  Description: Pipeline 缓存管理器，支持断点续传
               管理 progress.json 的创建/加载/保存
               页级和 DOCX 级别的状态管理
               hash 校验、线程安全写入

Version: 1.0
  Date: 2026-04-23
  Description: 初始版本，基础结构
'''

import os
import json
import hashlib
import shutil
import threading
from pathlib import Path
from datetime import datetime

from core.logUtils import log as _log


class CacheManager:
    """Pipeline 断点续传缓存管理器。"""

    def __init__(self, cachePath: str, inputPath: str, imageMode: str, verbose: bool = False):
        self.cachePath = cachePath
        self.inputPath = inputPath
        self.imageMode = imageMode
        self.verbose = verbose
        self._lock = threading.RLock()
        self._dirtyCount = 0  # 脏计数器，控制保存频率
        self._saveInterval = 5  # 每 5 次 updateImageResult 保存一次
        self._data: dict = {}

    def _computeFileHash(self) -> str:
        h = hashlib.sha256()
        with open(self.inputPath, "rb") as f:
            while chunk := f.read(8192):
                h.update(chunk)
        return f"sha256:{h.hexdigest()}"

    def _createNew(self) -> dict:
        return {
            "version": "3.0",
            "createdAt": datetime.now().isoformat(),
            "inputFile": self.inputPath,
            "inputHash": self._computeFileHash(),
            "fileType": Path(self.inputPath).suffix.lstrip("."),
            "imageMode": self.imageMode,
            "pages": [],
            "docx": None,
        }

    def _normalizeImage(self, img: dict) -> dict:
        normalized = dict(img)
        oldStatus = normalized.pop("status", None)
        normalized["analysisStatus"] = normalized.get("analysisStatus") or oldStatus or "pending"
        normalized.setdefault("analysisResult", None)
        normalized.setdefault("analysisUpdatedAt", None)
        return normalized

    def _normalizePage(self, page: dict) -> dict:
        normalized = dict(page)
        oldStatus = normalized.pop("status", None)
        if "parseStatus" not in normalized:
            normalized["parseStatus"] = "completed" if oldStatus == "completed" else (oldStatus or "pending")
        normalized.setdefault("rawMarkdown", "")
        if normalized.get("finalMarkdown") is None:
            normalized["finalMarkdown"] = normalized.get("rawMarkdown", "")
        normalized["images"] = [self._normalizeImage(img) for img in normalized.get("images", [])]
        normalized.setdefault("translationStatus", "pending")
        normalized.setdefault("translatedContent", None)
        normalized.setdefault("translatedAt", None)
        return normalized

    def _migrateCacheData(self, data: dict) -> dict:
        migrated = dict(data)
        migrated["version"] = "3.0"
        migrated["pages"] = [self._normalizePage(page) for page in migrated.get("pages", [])]
        migrated.setdefault("docx", data.get("docx"))
        migrated.setdefault("imageMode", self.imageMode)
        migrated.setdefault("fileType", Path(self.inputPath).suffix.lstrip("."))
        return migrated

    def loadOrCreate(self) -> dict:
        if os.path.isfile(self.cachePath):
            try:
                with open(self.cachePath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                assert "version" in data
                assert "inputHash" in data
                currentHash = self._computeFileHash()
                if data["inputHash"] != currentHash:
                    _log("输入文件已变更，清空缓存", "WARN", True, tag="[CacheManager]")
                    self._data = self._createNew()
                    self.save()
                    return self._data
                self._data = data
                migrated = self._migrateCacheData(data)
                self._data = migrated
                if migrated != data:
                    self.save()
                _log(f"加载已有缓存: {len(self.getCompletedPages())} 页已完成", "INFO", self.verbose, tag="[CacheManager]")
                return self._data
            except (json.JSONDecodeError, AssertionError, KeyError) as e:
                _log(f"缓存文件损坏({e})，重新创建", "WARN", True, tag="[CacheManager]")
                self._data = self._createNew()
                self.save()
                return self._data
        else:
            self._data = self._createNew()
            self.save()
            _log("创建新缓存", "INFO", self.verbose, tag="[CacheManager]")
            return self._data

    def save(self) -> None:
        with self._lock:
            cacheDir = os.path.dirname(self.cachePath)
            os.makedirs(cacheDir, exist_ok=True)
            with open(self.cachePath, "w", encoding="utf-8") as f:
                json.dump(self._data, f, ensure_ascii=False, indent=2)

    def clean(self) -> None:
        cacheDir = os.path.dirname(self.cachePath)
        if os.path.isdir(cacheDir):
            shutil.rmtree(cacheDir)
            _log(f"已删除缓存目录: {cacheDir}", "INFO", self.verbose, tag="[CacheManager]")

    # ============================================================
    # 页级操作（PDF/Image）
    # ============================================================

    def addPage(self, pageNum: int, rawMarkdown: str, images: list[dict]) -> None:
        with self._lock:
            normalizedImages = [self._normalizeImage(img) for img in images]
            page = {
                "pageNum": pageNum,
                "parseStatus": "completed",
                "rawMarkdown": rawMarkdown,
                "images": normalizedImages,
                "finalMarkdown": rawMarkdown,
                "translationStatus": "pending",
                "translatedContent": None,
                "translatedAt": None,
            }
            self._data["pages"] = [p for p in self._data["pages"] if p.get("pageNum") != pageNum]
            self._data["pages"].append(page)
            self._data["pages"].sort(key=lambda p: p["pageNum"])
            self.save()

    def updateImageResult(self, pageNum: int, imageId: str | None, result: dict | None) -> None:
        with self._lock:
            page = self._findPage(pageNum)
            if page is None:
                return
            if imageId is None:
                self.save()
                return
            for img in page["images"]:
                if img["imageId"] == imageId:
                    img["analysisStatus"] = "completed" if result is not None else "failed"
                    img["analysisResult"] = result
                    img["analysisUpdatedAt"] = datetime.now().isoformat()
                    break
            self.save()

    def finalizePage(self, pageNum: int, finalMarkdown: str) -> None:
        with self._lock:
            page = self._findPage(pageNum)
            if page is None:
                return
            page["parseStatus"] = "completed"
            page["finalMarkdown"] = finalMarkdown
            self.save()

    def markPageWritten(self, pageNum: int) -> None:
        with self._lock:
            page = self._findPage(pageNum)
            if page is None:
                return
            page["written"] = True
            self.save()

    def updateTranslationResult(self, pageNum: int, translatedContent: str | None) -> None:
        with self._lock:
            page = self._findPage(pageNum)
            if page is None:
                return
            page["translatedContent"] = translatedContent
            page["translatedAt"] = datetime.now().isoformat()
            page["translationStatus"] = "completed" if translatedContent is not None else "failed"
            self.save()

    def getTranslationStatus(self, pageNum: int) -> str | None:
        page = self._findPage(pageNum)
        if page is None:
            return None
        return page.get("translationStatus")

    def getPage(self, pageNum: int) -> dict | None:
        return self._findPage(pageNum)

    def getCompletedPages(self) -> list[int]:
        return [p["pageNum"] for p in self._data["pages"] if p.get("parseStatus") == "completed"]

    def findBreakpoint(self) -> int:
        if not self._data["pages"]:
            return 1
        for p in sorted(self._data["pages"], key=lambda item: item["pageNum"]):
            if p.get("parseStatus") != "completed":
                return p["pageNum"]
        return max(p["pageNum"] for p in self._data["pages"]) + 1

    def _findPage(self, pageNum: int) -> dict | None:
        for p in self._data["pages"]:
            if p["pageNum"] == pageNum:
                return p
        return None

    def resetRunningStates(self) -> None:
        with self._lock:
            changed = False
            for page in self._data.get("pages", []):
                if page.get("parseStatus") == "running":
                    page["parseStatus"] = "pending"
                    changed = True
                if page.get("translationStatus") == "running":
                    page["translationStatus"] = "pending"
                    changed = True
                for img in page.get("images", []):
                    if img.get("analysisStatus") == "running":
                        img["analysisStatus"] = "pending"
                        changed = True
            if changed:
                self.save()

    def iterPendingImages(self):
        for page in sorted(self._data.get("pages", []), key=lambda item: item["pageNum"]):
            if page.get("parseStatus") != "completed":
                continue
            for img in page.get("images", []):
                if img.get("analysisStatus", "pending") in ("pending", "failed"):
                    yield page, img

    def iterPendingTranslations(self):
        for page in sorted(self._data.get("pages", []), key=lambda item: item["pageNum"]):
            if page.get("parseStatus") != "completed":
                continue
            if page.get("translationStatus", "pending") in ("pending", "failed"):
                yield page

    def hasParsedPages(self) -> bool:
        return any(p.get("parseStatus") == "completed" for p in self._data.get("pages", []))

    def markTranslationRunning(self, pageNum: int) -> None:
        with self._lock:
            page = self._findPage(pageNum)
            if page is None:
                return
            page["translationStatus"] = "running"
            self.save()

    def markImageRunning(self, pageNum: int, imageId: str) -> None:
        with self._lock:
            page = self._findPage(pageNum)
            if page is None:
                return
            for img in page.get("images", []):
                if img.get("imageId") == imageId:
                    img["analysisStatus"] = "running"
                    break
            self.save()

    # ============================================================
    # DOCX 操作
    # ============================================================

    def setDocxRaw(self, rawMarkdown: str, images: list[dict]) -> None:
        with self._lock:
            self._data["docx"] = {
                "status": "parsed",
                "rawMarkdown": rawMarkdown,
                "images": images,
                "finalMarkdown": None,
            }
            self.save()

    def updateDocxImageResult(self, imageId: str, result: dict | None) -> None:
        with self._lock:
            docx = self._data.get("docx")
            if docx is None:
                return
            for img in docx["images"]:
                if img["imageId"] == imageId:
                    img["status"] = "completed" if result is not None else "failed"
                    img["analysisResult"] = result
                    break
            if docx["status"] == "parsed":
                docx["status"] = "analyzing"
            self.save()

    def setDocxFinal(self, finalMarkdown: str) -> None:
        with self._lock:
            docx = self._data.get("docx")
            if docx is None:
                return
            docx["status"] = "completed"
            docx["finalMarkdown"] = finalMarkdown
            self.save()

    # ============================================================
    # 重建
    # ============================================================

    def rebuildMarkdown(self, upToPage: int | None = None) -> str:
        parts = []
        pages = sorted(self._data.get("pages", []), key=lambda item: item["pageNum"])
        for p in pages:
            if upToPage is not None and p["pageNum"] > upToPage:
                break
            if p.get("parseStatus") != "completed":
                continue
            parts.append(p.get("finalMarkdown") or p.get("rawMarkdown", ""))
        return "\n\n---\n\n".join(parts)

    def rebuildTranslationMarkdown(self) -> str:
        parts = []
        pages = sorted(self._data.get("pages", []), key=lambda item: item["pageNum"])
        for p in pages:
            if p.get("parseStatus") != "completed":
                continue
            translated = p.get("translatedContent")
            if translated:
                parts.append(translated)
            else:
                parts.append("<!-- [翻译失败，保留原文] -->\n\n" + p.get("rawMarkdown", ""))
        return "\n\n---\n\n".join(parts)
