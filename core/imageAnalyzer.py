'''
Author: wilbur
Version: 1.4
  Date: 2026-07-28
  Description: 迁移至 core 包，日志改为 core.logUtils 统一实现并补 [ImageAnalyzer] tag

Version: 1.3
  Date: 2026-04-23
  Description: 新增 analyzeImageWithCallback() 带回调的单图分析方法

Version: 1.2
  Date: 2026-04-21
  Description: 新增 retryDelay 参数，默认 10 次重试每次 15 秒固定间隔，替代指数退避

Version: 1.1
  Date: 2026-04-14
  Description: 引入线程安全 Session 管理；修复响应解析健壮性；重试添加随机抖动

Version: 1.0
  Date: 2026-04-13
  Description: 图像理解 API 调用模块，支持并发分析、重试降级、响应解析
'''

import json
import re
import time
import base64
from concurrent.futures import ThreadPoolExecutor, as_completed
import threading

import requests

from core.logUtils import log as _log


# ============================================================
# 常量
# ============================================================

SYSTEM_PROMPT = """
你是一个严格的图像结构解析器，只输出可解析的 JSON，不输出任何额外说明、Markdown、代码块、注释或前后缀文本。

你的目标不是“尽量保留原始排版”，而是“输出稳定、可渲染、可解析的结构化结果”。

如果输出 Mermaid：
- 必须保证语法尽可能安全、简洁、可渲染
- 只能使用 Mermaid flowchart 的安全子集
- 节点 ID 只能使用英文字母、数字、下划线，例如 n1, n2, decision_1
- 节点文本必须做清洗和简化，禁止原样保留复杂数学公式、上下标、花括号、方括号、尖括号、引号、竖线、反引号等高风险符号
- 节点文本只保留可读的简短语义，例如 “输入”, “查询向量”, “键值缓存”, “Top-k选择”, “注意力输出”
- 不要输出 LaTeX，不要输出 Markdown，不要输出 HTML
- 不要输出 mermaid 代码围栏
- 不要在任何一行前面加 > 或其他引用符号
- 连线只使用 -->、-->|条件|、-.-> 这类基础写法
- 如果图过于复杂、公式过多、无法稳定转成 Mermaid，则宁可判定为 document，也不要输出高风险 Mermaid

如果输出 document：
- 侧重 OCR 和结构化摘要
- summary 要适合向量检索/RAG
"""

USER_PROMPT = """
请分析输入图片，并严格按以下规则输出。

【任务】
先判断图片类型：

A. flowchart
满足以下任一特征即可判断为 flowchart：
- 存在多个节点/框/圆角框/菱形
- 节点之间存在箭头、连线、流程方向
- 明显表达步骤、判断、数据流、模块关系

B. document
如果不满足 flowchart，则归为 document

--------------------------------------------------
【当 type = "flowchart" 时】

你的任务：
1. 提取主要节点
2. 提取主要连接关系
3. 生成“可渲染优先”的 Mermaid flowchart 代码

强制要求：
- 只输出 Mermaid flowchart，不要解释
- 只能使用 flowchart TD 或 flowchart LR
- 所有节点都使用安全 ID，例如 n1, n2, n3
- 节点定义格式示例：
  n1[输入]
  n2[查询向量]
  n3{是否命中}
- 节点文本必须“语义清洗”后再写入，不能照抄复杂公式
- 禁止在节点文本中使用以下内容：
  - >
  - <
  - { }
  - [ ]
  - ( )
  - "
  - '
  - `
  - |
  - ;（尽量不用）
  - LaTeX 公式
  - 上下标表达
  - 复杂变量名，如 q_t,i^R 这类原始公式
- 将复杂数学符号改写成自然语言短语，例如：
  - q / query 相关 => 查询向量
  - k / key 相关 => 键向量
  - v / value 相关 => 值向量
  - RoPE => 位置编码
  - Top-k => Top-k选择
  - Multi-Query Attention => 多查询注意力
- 连线尽量简单：
  - n1 --> n2
  - n2 -->|yes| n3
  - n2 -->|no| n4
- 不要为了“完整复现原图”而引入复杂语法
- 如果图中公式太多，优先保留流程语义，不保留公式形式
- 如果无法稳定生成安全 Mermaid，则改判为 document

flowchart 输出字段要求：
- type = "flowchart"
- content = Mermaid 源码字符串
- summary = 1-2句中文概述，简述流程做什么

--------------------------------------------------
【当 type = "document" 时】

你的任务：
1. 提取全部可见文字
2. 生成适合 RAG 的结构化摘要

document 输出字段要求：
- type = "document"
- content = OCR 提取出的文字，按阅读顺序组织
- summary = 中文结构化摘要，包含：
  - 主题
  - 关键要点列表
  - 检索关键词

--------------------------------------------------
【最终输出格式】

必须只输出一个 JSON 对象，且保证可被 json.loads 解析。

格式如下：

{
  "type": "flowchart" 或 "document",
  "content": "...",
  "summary": "..."
}

--------------------------------------------------
【绝对禁止】
- 不要输出 Markdown
- 不要输出 ``` 代码块
- 不要输出 mermaid 代码围栏
- 不要输出前置解释或后置解释
- 不要输出以 > 开头的引用内容
- 不要输出 JSON 之外的任何字符

【输出前自检】
如果 type = "flowchart"，输出前必须检查：
1. content 第一行是否为 flowchart TD 或 flowchart LR
2. 是否包含 ``` 或 > 或多余解释；如果有，删除
3. 是否存在复杂公式、花括号、尖括号、嵌套中括号；如果有，改写为简单中文标签
4. 是否每个节点都使用安全 ID（如 n1, n2）
5. 如果仍不确定 Mermaid 能否渲染，则改为 document
"""

# ============================================================
# 响应解析
# ============================================================

def _parseApiResponse(content: str) -> dict:
    """解析 API 返回的 content 字符串为 {type, content, summary} dict。

    解析策略（按优先级）：
    1. 清理 <think.../think> 和 <thinking.../thinking> 标签
    2. 直接 JSON 解析
    3. 移除 markdown 代码块后重试
    4. 使用 json.JSONDecoder.raw_decode 精确提取
    5. 降级为原始文本
    """
    # 合法的 type 值
    _VALID_TYPES = {"flowchart", "document"}

    def _normalizeType(rawType: str) -> str:
        t = rawType.strip().lower()
        return t if t in _VALID_TYPES else "document"

    def _buildResult(obj: dict) -> dict:
        return {
            "type": _normalizeType(obj.get("type", "document")),
            "content": obj.get("content", ""),
            "summary": obj.get("summary", ""),
        }

    # 预处理：移除 thinking 标签（支持 <think|thinking> 两种变体）
    cleaned = re.sub(r'<(?:think|thinking)[\s\S]*?</(?:think|thinking)\s*>', '', content).strip()

    # 尝试 1: 直接 JSON 解析
    try:
        result = json.loads(cleaned)
        if isinstance(result, dict) and "type" in result:
            return _buildResult(result)
    except json.JSONDecodeError:
        pass

    # 尝试 2: 移除 markdown 代码块包裹后重试
    codeBlockMatch = re.search(r'```(?:json)?\s*([\s\S]*?)\s*```', cleaned)
    if codeBlockMatch:
        try:
            result = json.loads(codeBlockMatch.group(1).strip())
            if isinstance(result, dict) and "type" in result:
                return _buildResult(result)
        except json.JSONDecodeError:
            pass

    # 尝试 3: 使用 json.JSONDecoder.raw_decode 精确提取（替代手写括号匹配）
    decoder = json.JSONDecoder()
    start = cleaned.find('{')
    if start != -1:
        # 尝试从每个 '{' 位置开始解析
        for pos in range(start, len(cleaned)):
            if cleaned[pos] != '{':
                continue
            try:
                obj, _ = decoder.raw_decode(cleaned, pos)
                if isinstance(obj, dict) and "type" in obj:
                    return _buildResult(obj)
            except json.JSONDecodeError:
                continue

    # 降级: 原始文本作为 content
    _log(f"JSON 解析失败，使用原始文本作为 content", "WARN", True, tag="[ImageAnalyzer]")
    return {
        "type": "document",
        "content": content,
        "summary": "",
    }


# ============================================================
# 主类
# ============================================================

class ImageAnalyzer:
    """图像理解 API 调用器，支持单张/并发分析、重试降级。"""

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
        # 线程安全的 Session 管理：每个线程独立的 requests.Session
        self._threadLocal = threading.local()

    def _getSession(self) -> requests.Session:
        """获取当前线程专属的 requests.Session，惰性创建。"""
        if not hasattr(self._threadLocal, 'session'):
            session = requests.Session()
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

    def _buildRequestPayload(self, base64Data: str, imageFormat: str = "png") -> dict:
        """构造 API 请求体。"""
        return {
            "model": self.model,
            "stream": False,
            "enable_thinking": False,
            "stop": ["<|im_end|>"],
            "max_tokens": 31000,
            "temperature": 0.1,
            "top_p": 0.8,
            "top_k": 20,
            "presence_penalty": 0.3,
            "repetition_penalty": 1.1,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": USER_PROMPT,
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:image/{imageFormat};base64,{base64Data}"
                            },
                        },
                    ],
                },
            ],
        }

    def _callApi(self, base64Data: str, imageFormat: str = "png") -> dict | None:
        """调用图像理解 API，带重试和固定间隔。"""
        payload = self._buildRequestPayload(base64Data, imageFormat)
        session = self._getSession()

        for attempt in range(self.maxRetry):
            try:
                _log(f"API 请求 (尝试 {attempt + 1}/{self.maxRetry})", "DEBUG", self.verbose, tag="[ImageAnalyzer]")
                response = session.post(
                    self.apiUrl,
                    json=payload,
                    timeout=120,
                )
                response.raise_for_status()
                data = response.json()

                # 安全提取 content，处理 None 和 reasoning_content 字段
                message = data.get("choices", [{}])[0].get("message", {})
                content = message.get("content")
                if content is None:
                    content = message.get("reasoning_content", "")
                if not isinstance(content, str):
                    content = str(content) if content else ""

                _log(f"API 响应成功，内容长度: {len(content)}", "DEBUG", self.verbose, tag="[ImageAnalyzer]")
                return _parseApiResponse(content)

            except requests.exceptions.RequestException as e:
                detail = ""
                if isinstance(e, requests.exceptions.HTTPError) and e.response is not None:
                    try:
                        detail = e.response.text[:500]
                    except Exception:
                        detail = "(无法读取响应体)"
                _log(f"API 请求失败 (尝试 {attempt + 1}/{self.maxRetry}): {e}，详情: {detail}，{self.retryDelay}s 后重试", "WARN", True, tag="[ImageAnalyzer]")
                if attempt < self.maxRetry - 1:
                    time.sleep(self.retryDelay)
            except (KeyError, IndexError, json.JSONDecodeError) as e:
                _log(f"API 响应解析失败: {e}，{self.retryDelay}s 后重试", "WARN", True, tag="[ImageAnalyzer]")
                if attempt < self.maxRetry - 1:
                    time.sleep(self.retryDelay)

        _log(f"API 调用全部失败（{self.maxRetry} 次重试用尽）", "ERROR", True, tag="[ImageAnalyzer]")
        return None

    def analyzeImage(self, imageSource: dict) -> dict | None:
        """分析单张图片。

        参数:
            imageSource: dict，包含:
                - 'path': 图片文件绝对路径（优先）
                - 'base64': base64 编码的图片数据（备选）
                - 'format': 图片格式（默认 'png'）

        返回:
            {type, content, summary} 或 None（全部重试失败）
        """
        imageFormat = imageSource.get("format", "png")

        if "path" in imageSource and imageSource["path"]:
            filePath = imageSource["path"]
            _log(f"分析图片: {filePath}", "INFO", self.verbose, tag="[ImageAnalyzer]")
            try:
                with open(filePath, "rb") as f:
                    imgData = f.read()
                base64Data = base64.b64encode(imgData).decode("utf-8")
            except IOError as e:
                _log(f"读取图片失败: {filePath} - {e}", "ERROR", True, tag="[ImageAnalyzer]")
                return None
        elif "base64" in imageSource and imageSource["base64"]:
            base64Data = imageSource["base64"]
            _log(f"分析图片: base64 数据 (长度: {len(base64Data)})", "INFO", self.verbose, tag="[ImageAnalyzer]")
        else:
            _log("imageSource 缺少 'path' 或 'base64'", "ERROR", True, tag="[ImageAnalyzer]")
            return None

        return self._callApi(base64Data, imageFormat)

    def analyzeImages(self, imageSources: list[dict]) -> list[dict | None]:
        """并发分析多张图片，保持原始顺序。

        参数:
            imageSources: list[dict]，每个元素格式同 analyzeImage 的 imageSource

        返回:
            与 imageSources 等长的结果列表，保持原始顺序
        """
        if not imageSources:
            return []

        _log(f"开始并发分析 {len(imageSources)} 张图片（并发数: {self.maxConcurrent}）", "INFO", self.verbose, tag="[ImageAnalyzer]")

        results = [None] * len(imageSources)

        with ThreadPoolExecutor(max_workers=self.maxConcurrent) as executor:
            futureToIndex = {
                executor.submit(self.analyzeImage, src): idx
                for idx, src in enumerate(imageSources)
            }
            for future in as_completed(futureToIndex):
                idx = futureToIndex[future]
                try:
                    results[idx] = future.result()
                except Exception as e:
                    _log(f"图片分析异常 (index={idx}): {e}", "ERROR", True, tag="[ImageAnalyzer]")
                    results[idx] = None

        successCount = sum(1 for r in results if r is not None)
        _log(f"图片分析完成: {successCount}/{len(imageSources)} 成功", "INFO", self.verbose, tag="[ImageAnalyzer]")
        return results

    def analyzeImageWithCallback(
        self,
        imageSource: dict,
        callback: "callable | None" = None,
    ) -> dict | None:
        """分析单张图片，完成后调用 callback(result)。

        参数:
            imageSource: 同 analyzeImage 的 imageSource 参数
            callback: 可选回调函数，签名为 callback(result: dict | None)

        返回:
            {type, content, summary} 或 None
        """
        result = self.analyzeImage(imageSource)
        if callback:
            callback(result)
        return result
