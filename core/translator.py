'''
Author: wilbur
Version: 1.8
  Date: 2026-08-03
  Description: TRANSLATION_SYSTEM_PROMPT 新增第7条：硬约束图片引用 ![](path) 原样保留
               （alt 可译、路径不变、不去感叹号），保障 pdf 任务渲染 _cn.pdf 图片不丢失

Version: 1.7
  Date: 2026-07-28
  Description: 迁移至 core 包，日志改为 core.logUtils 统一实现并补 [Translator] tag

Version: 1.6
  Date: 2026-05-21
  Description: verbose 打印翻译原始响应 JSON 和 content

Version: 1.5
  Date: 2026-04-25
  Description: 新增 Anthropic Messages API 支持，根据 URL 自动检测 API 类型

Version: 1.4
  Date: 2026-04-25
  Description: 重写 _stripThinkingTags 为 _stripThinkingProcess，支持裸闭标签格式的思考过程剥离

Version: 1.0
  Date: 2026-04-24
  Description: Markdown 文本翻译器，支持并发翻译和重试
'''

import json
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
import requests.adapters

from core.logUtils import log as _log


TRANSLATION_SYSTEM_PROMPT = (
    "你是一个专业的学术论文翻译专家。请将以下Markdown内容翻译成中文。\n\n"
    "要求：\n"
    "1. 保持Markdown格式不变\n"
    "2. 术语翻译准确，符合学术规范\n"
    "3. 数学公式、代码块保持原样，只翻译注释和说明文字\n"
    "4. 图表标题和说明文字需要翻译\n"
    "5. 保持原文的层次结构（标题、列表、引用等）\n"
    "6. 翻译流畅，符合中文表达习惯\n"
    "7. 图片引用 ![](路径) 必须原样保留，不得删除、改写路径或去掉感叹号；alt 文本可翻译但路径不变"
)


def _logRawJson(title: str, data: dict, verbose: bool) -> None:
    if not verbose:
        return
    try:
        raw = json.dumps(data, ensure_ascii=False, default=str, indent=2)
    except Exception:
        raw = str(data)
    _log(f"{title}:\n{raw}", "DEBUG", True, tag="[Translator]")


THINKING_HEAD_KEYWORDS = (
    # 英文
    "thinking process",
    "<think",
    "<thinking",
    "let me analyze",
    "let me start by",
    "let me first",
    "let me think",
    "i need to translate",
    "i'll translate",
    "i should translate",
    # 中文
    "思考过程",
    "让我分析",
    "让我先",
    "让我思考",
    "我需要翻译",
    "首先，我需要",
    "我先分析",
)

_PAIRED_THINK_RE = re.compile(
    r"<think(?:ing)?>.*?</think(?:ing)?>",
    flags=re.DOTALL | re.IGNORECASE,
)

_PRIMARY_CUT_RE = re.compile(
    r"^.*?</think(?:ing)?>\s*\n\s*\n[ \t]*(?=---|#)",
    flags=re.DOTALL | re.IGNORECASE,
)

_LEADING_SEPARATOR_RE = re.compile(r"^(\s*---\s*\n+)+")


def _looksLikeThinkingHead(text: str) -> bool:
    head = text.lstrip()[:200].lower()
    return any(kw in head for kw in THINKING_HEAD_KEYWORDS)


def _findLastCloseTagEnd(text: str) -> int:
    lastEnd = -1
    for tag in ("</thinking>", "</think" + ">"):
        idx = text.rfind(tag)
        if idx >= 0:
            end = idx + len(tag)
            if end > lastEnd:
                lastEnd = end
    return lastEnd


def _stripThinkingProcess(text: str) -> str:
    """剥离 LLM 翻译响应中的思考过程。

    处理顺序：
    1. 成对 <think(...)>...</think(...)> 标签剥离
    2. 若开头仍像思考过程：优先用「闭标签 + 空行 + 分隔符」截断；
       否则退化截到最后一个闭标签；都没有则原样返回。
    3. 去前导 --- 分隔符和首尾空白。
    """
    if not text or not text.strip():
        return ""

    # Step 1: 成对标签
    cleaned = _PAIRED_THINK_RE.sub("", text)

    # Step 2: 裸闭标签处理（仅在开头特征命中时）
    if _looksLikeThinkingHead(cleaned):
        m = _PRIMARY_CUT_RE.match(cleaned)
        if m:
            cleaned = cleaned[m.end():]
        else:
            lastEnd = _findLastCloseTagEnd(cleaned)
            if lastEnd > 0:
                cleaned = cleaned[lastEnd:]
                _log(
                    "思考过程剥离走退化路径（未匹配到闭标签+分隔符组合）",
                    "WARN", True, tag="[Translator]",
                )
            else:
                _log(
                    "响应开头疑似思考但未找到闭标签，原样返回",
                    "WARN", True, tag="[Translator]",
                )

    # Step 3: 收尾清理
    cleaned = _LEADING_SEPARATOR_RE.sub("", cleaned)
    return cleaned.strip()


# 保留旧名兼容
_stripThinkingTags = _stripThinkingProcess


def _detectApiType(apiUrl: str) -> str:
    """根据 URL 自动检测 API 类型。"""
    urlLower = apiUrl.lower()
    if "/v1/messages" in urlLower or urlLower.endswith("/messages"):
        return "anthropic"
    return "openai"


class Translator:
    """Markdown 文本翻译器，支持 OpenAI / Anthropic 两种 API 格式。"""

    def __init__(
        self,
        apiUrl: str,
        apiKey: str,
        model: str = "qwen3.5",
        maxConcurrent: int = 3,
        maxRetry: int = 10,
        retryDelay: float = 15.0,
        verbose: bool = False,
    ):
        self.apiUrl = apiUrl
        self.apiKey = apiKey
        self.model = model
        self.maxConcurrent = maxConcurrent
        self.maxRetry = maxRetry
        self.retryDelay = retryDelay
        self.verbose = verbose
        self.apiType = _detectApiType(apiUrl)
        self._threadLocal = threading.local()
        _log(f"翻译 API 类型: {self.apiType} (URL: {apiUrl})", "INFO", verbose, tag="[Translator]")

    def _getSession(self) -> requests.Session:
        """获取当前线程专属的 requests.Session，惰性创建。"""
        if not hasattr(self._threadLocal, "session"):
            session = requests.Session()
            if self.apiType == "anthropic":
                session.headers.update({
                    "x-api-key": self.apiKey,
                    "anthropic-version": "2023-06-01",
                    "Content-Type": "application/json",
                })
            else:
                session.headers.update({
                    "Authorization": f"Bearer {self.apiKey}",
                    "Content-Type": "application/json",
                })
            adapter = requests.adapters.HTTPAdapter(
                pool_connections=4,
                pool_maxsize=4,
                max_retries=0,
            )
            session.mount("http://", adapter)
            session.mount("https://", adapter)
            self._threadLocal.session = session
        return self._threadLocal.session

    def _buildAnthropicPayload(self, text: str) -> dict:
        """构造 Anthropic Messages API 请求体。"""
        return {
            "model": self.model,
            "max_tokens": 11000,
            "temperature": 0.1,
            "top_p": 0.8,
            "system": TRANSLATION_SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": f"待翻译内容：\n---\n{text}\n---"},
            ],
        }

    def _buildOpenAiPayload(self, text: str) -> dict:
        """构造 OpenAI-compatible API 请求体。"""
        return {
            "model": self.model,
            "stream": False,
            "enable_thinking": False,
            "stop": ["<|im_end|>"],
            "max_tokens": 11000,
            "temperature": 0.1,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 0.3,
            "repetition_penalty": 1.1,
            "messages": [
                {"role": "system", "content": TRANSLATION_SYSTEM_PROMPT},
                {"role": "user", "content": f"待翻译内容：\n---\n{text}\n---"},
            ],
        }

    def _buildRequestPayload(self, text: str) -> dict:
        """根据 API 类型构造请求体。"""
        if self.apiType == "anthropic":
            return self._buildAnthropicPayload(text)
        return self._buildOpenAiPayload(text)

    def _extractContent(self, data: dict) -> str:
        """根据 API 类型从响应中提取文本内容。"""
        if self.apiType == "anthropic":
            contentBlocks = data.get("content", [])
            texts = [block.get("text", "") for block in contentBlocks if block.get("type") == "text"]
            return "\n".join(texts) if texts else ""
        else:
            message = data.get("choices", [{}])[0].get("message", {})
            content = message.get("content")
            if content is None:
                content = message.get("reasoning_content", "")
            if not isinstance(content, str):
                content = str(content) if content else ""
            return content

    def _callApi(self, text: str) -> str | None:
        """调用翻译 API，带重试和固定间隔。"""
        payload = self._buildRequestPayload(text)
        session = self._getSession()

        for attempt in range(self.maxRetry):
            try:
                _log(f"翻译请求 (尝试 {attempt + 1}/{self.maxRetry})", "DEBUG", self.verbose, tag="[Translator]")
                response = session.post(
                    self.apiUrl,
                    json=payload,
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()
                _logRawJson("翻译响应原始 JSON", data, self.verbose)

                content = self._extractContent(data)
                if self.verbose:
                    _log(f"翻译响应原始 content:\n{content}", "DEBUG", True, tag="[Translator]")

                result = _stripThinkingTags(content)
                _log(f"翻译响应成功，内容长度: {len(result)}", "DEBUG", self.verbose, tag="[Translator]")
                return result

            except requests.exceptions.RequestException as e:
                detail = ""
                if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                    try:
                        detail = e.response.text[:500]
                    except Exception:
                        detail = "(无法读取响应体)"
                _log(
                    f"翻译请求失败 (尝试 {attempt + 1}/{self.maxRetry}): {e}，详情: {detail}，"
                    f"{self.retryDelay}s 后重试", "WARN", True, tag="[Translator]",
                )
                if attempt < self.maxRetry - 1:
                    time.sleep(self.retryDelay)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                _log(f"翻译响应解析失败: {e}，{self.retryDelay}s 后重试", "WARN", True, tag="[Translator]")
                if attempt < self.maxRetry - 1:
                    time.sleep(self.retryDelay)

        _log(f"翻译 API 调用全部失败（{self.maxRetry} 次重试用尽）", "ERROR", True, tag="[Translator]")
        return None

    def translate(self, text: str) -> str | None:
        """翻译单段 Markdown 文本。

        参数:
            text: 待翻译的 Markdown 文本

        返回:
            翻译后的中文文本，或 None（全部重试失败）
        """
        if not text or not text.strip():
            return text
        _log(f"翻译文本 (长度: {len(text)})", "INFO", self.verbose, tag="[Translator]")
        return self._callApi(text)

    def translateTexts(self, texts: list[str]) -> list[str | None]:
        """并发翻译多段 Markdown 文本，保持原始顺序。

        参数:
            texts: 待翻译文本列表

        返回:
            与 texts 等长的结果列表，保持原始顺序
        """
        if not texts:
            return []

        _log(f"开始并发翻译 {len(texts)} 段文本（并发数: {self.maxConcurrent}）", "INFO", self.verbose, tag="[Translator]")

        results: list[str | None] = [None] * len(texts)

        with ThreadPoolExecutor(max_workers=self.maxConcurrent) as executor:
            futureToIndex = {
                executor.submit(self.translate, txt): idx
                for idx, txt in enumerate(texts)
            }
            for future in as_completed(futureToIndex):
                idx = futureToIndex[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    _log(f"翻译异常 (index={idx}): {e}", "ERROR", True, tag="[Translator]")
                    results[idx] = None

        successCount = sum(1 for r in results if r is not None)
        _log(f"翻译完成: {successCount}/{len(texts)} 成功", "INFO", self.verbose, tag="[Translator]")
        return results
