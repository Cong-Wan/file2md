# files2MD 项目整理执行计划

> 日期：2026-07-28
> 状态：**已完成**（2026-07-28 执行完毕，提交 befc9d2..HEAD）
> 计划审核报告：`docs/codeReview/260728_planReview.md`（修正条款已并入本文）

## 对齐结论（用户已确认）

| # | 问题 | 结论 |
|---|------|------|
| 1 | 目录结构 | 方案 B：按职责分包（`core/`） |
| 2 | parseFlowApi 独立 CLI | 砍掉，只留库函数，统一从 pipeline.py 进 |
| 3 | 死代码/冗余文件 | 直接删，git 历史兜底 |
| 4 | legacy 参数（--skip-image-analysis / --reanalyze-images / --enable-translation） | 砍掉，只留 --tasks |
| 5 | 旧缓存（progress.json）兼容 | 不保留，删迁移逻辑，字段统一为 parseStatus/analysisStatus |

## 现状诊断摘要

- **死代码 ~400 行**：`_parseAndDispatchLoop`、`_finalizePdfPipeline`、`_shutdownExecutors`、`_parsePdfPages`、`_runReanalyze`、`_translateAndWrite`、`processImageAnalysis`、`_stripOldAnalysisBlocks`、`parsePdfOrImage`、`orderedWriter.py`（整文件）
- **重复代码**：log 定义 ×6、图片格式推断 ×4、图片 info dict 构造 ×3、ImageAnalyzer 创建 ×4、Translator 创建 ×2、future 提交逻辑 ×2
- **逻辑绕**：env 配置回写 args 隐式传参、`enableTranslation` 冗余参数、DOCX 路径残留旧 `status` 字段
- **冗余文件**：`requirements.txt`（与 pyproject.toml 重复）、README 严重过时

## 目标结构

```
files2MD/
├── pipeline.py            # CLI 入口 + 主编排（预计 1414 → ~750 行）
├── core/
│   ├── __init__.py        # 空包标记 + 文件头
│   ├── logUtils.py        # 统一日志（消灭 6 份重复定义）
│   ├── cacheManager.py    # 删迁移逻辑后 ~300 行
│   ├── imageAnalyzer.py
│   ├── translator.py
│   ├── parseFlowApi.py    # 只留库函数（581 → ~380 行）
│   └── mdPostprocess.py   # MD 后处理（从 pipeline 抽出）
├── docxTools/             # 不动
├── pyproject.toml
└── README.md              # 重写
删除: orderedWriter.py、requirements.txt
```

---

## Step 0：基线确认

- [x] 0.1 `git status` 确认干净（已确认：工作区干净，基线提交 294dcad），当前分支直接改，git 历史兜底

## Step 1：建 core 包，迁移文件 + 统一日志

- [x] 1.1 `mkdir core`；新建 `core/__init__.py`（文件头 v1.0，说明"核心模块包"）
- [x] 1.2 `git mv cacheManager.py imageAnalyzer.py translator.py parseFlowApi.py core/`
- [x] 1.3 新建 `core/logUtils.py`（v1.0）：从 pipeline.py 提取 `log()`、`logSeparator()`；`log` 增加 `tag: str = ""` 参数，输出格式 `f"{timestamp} {prefix} {tag}{msg}"`（tag 非空时形如 `[ImageAnalyzer] `）
- [x] 1.4 新建 `core/mdPostprocess.py`（v1.0），从 pipeline.py 移入以下函数（去 `_` 前缀改公开）：
  - `IMAGE_REF_PATTERN`、`isDataUri`、`extractImageSources`、`buildAnalysisBlock`（顺手删 no-op 行 `content.replace("```", "```")`）、`insertAnalysisResults`、`buildFinalMarkdownFromCache`、`applyImageMode`
  - 新增 `guessImageFormat(path: str) -> str`（统一 png/jpeg/webp 推断）
- [x] 1.5 改 `core/cacheManager.py`：删本地 `_log`，改 `from core.logUtils import log as _log`；调用处补 tag `[CacheManager]`（先 grep 确认原前缀）
- [x] 1.6 改 `core/imageAnalyzer.py`：同上，tag `[ImageAnalyzer]`
- [x] 1.7 改 `core/translator.py`：同上，tag `[Translator]`；`_logRawJson` 保留
- [x] 1.8 改 `core/parseFlowApi.py`：删本地 `log`/`logSeparator`，改 import；调用处 tag 不变（原无 tag）
- [x] 1.9 改 `pipeline.py` 头部 import：`from core.imageAnalyzer import ImageAnalyzer`、`from core.cacheManager import CacheManager`、`from core.logUtils import log, logSeparator`、`from core.mdPostprocess import (...)`；函数内 `from parseFlowApi import ...` → `from core.parseFlowApi import ...`；删本地 `log`/`logSeparator` 定义
- **验证**：`uv run python -c "import pipeline"` 无报错；`uv run python pipeline.py --help` 正常输出

## Step 2：删死代码

- [x] 2.1 `git rm orderedWriter.py requirements.txt`
- [x] 2.2 pipeline.py 删除 9 个死函数：`parsePdfOrImage`、`_stripOldAnalysisBlocks`、`_translateAndWrite`、`processImageAnalysis`、`_parsePdfPages`、`_parseAndDispatchLoop`、`_finalizePdfPipeline`、`_shutdownExecutors`、`_runReanalyze`
- [x] 2.3 pipeline.py 删除已移入 mdPostprocess 的函数原定义（`_extractImageSources` 等），调用点改为新名
- [x] 2.4 `core/parseFlowApi.py` 删除：`convertPdfOrImage`、`runPipeline`、`buildArgParser`、`main`、`if __name__ == "__main__"`；清理孤立 import
  - ⚠️ **审核修正**：删除前对 `saveImageBlocks`、`blocksToMarkdown`、`logRawParseOutput` 等逐函数 grep 确认调用方，零调用方才删；已确认 `logRawParseOutput` 被 `parseSinglePage:499` 调用，**保留**
- [x] 2.5 连带清理 pipeline.py 中因删除而孤立的代码
- **验证**：`grep -rn "orderedWriter\|OrderedMarkdownWriter\|_runReanalyze\|processImageAnalysis\|convertPdfOrImage" --include="*.py" .` 零命中；`uv run python pipeline.py --help` 正常

## Step 3：去重复

- [x] 3.1 `_runParsePipelineTask` 中图片格式推断 if/elif → `guessImageFormat(absPath)`
- [x] 3.2 `_runParsePipelineTask` 中图片 info dict 构造 → 新增 pipeline 内 helper `buildImageInfo(pageNum, idx, absPath) -> dict`
- [x] 3.3 `_runImageTask`：手写 `ImageAnalyzer(...)` → `_createImageAnalyzer(ctx, apiKey)`；内联提交循环 → `_submitImageFutures(...)`
  - ⚠️ **审核修正**：先把 `_submitImageFutures` 签名改为接受 `(page, img)` 对列表 `pendingPairs`；`_runParsePipelineTask` 调用处传 `[(page, img) for img in images]`，`_runImageTask` 传 `pending`
- [x] 3.4 `_runTranslateTask`：手写 `Translator(...)` → `_createTranslator(...)`；内联提交 → `_submitTranslationFuture(...)`
- [x] 3.5 `_runDocx`：手写 `ImageAnalyzer(...)` → 复用 `_createImageAnalyzer`（签名适配：当前依赖 ctx，改为可接收 args/config）
- **验证**：`grep -n "ImageAnalyzer(\|Translator(" pipeline.py` 各只剩 1 处（helper 内）；`grep -rn "def log\|def _log" --include="*.py" . | grep -v .venv` 只剩 `core/logUtils.py`

## Step 4：逻辑理顺

- [x] 4.1 砍 legacy 参数：`buildArgParser` 删 `--skip-image-analysis`、`--reanalyze-images`、`--enable-translation`；`parse_tasks()` 简化为只解析 `--tasks`（空 → 默认 `parse,image`）；`runPipeline` 删"忽略兼容参数"的 WARN 分支；CLI epilog 示例同步删
- [x] 4.2 删 `_runPdfOrImage` 的 `enableTranslation` 冗余参数（签名 + `runPipeline` 调用点）；`runPipeline` 中翻译配置检查逻辑保留但不再产出该变量
- [x] 4.3 env 配置不再回写 args：新增 `PipelineConfig` dataclass（8 字段：image/pdf/translate 的 url/key/model）；`runPipeline` 构建后显式传给 `_runDocx`/`_runPdfOrImage`；`PdfPipelineContext` 增加 `config` 字段，下游从 `ctx.config` 读 API 配置（`ctx.args` 保留给 dpi/timeout/并发等 CLI 参数）
- [x] 4.4 状态字段统一：DOCX 图片 `status` → `analysisStatus`（`_runDocx` 2 处 + `cacheManager.updateDocxImageResult`）；docx 顶层 `status`（parsed/analyzing/completed 三态）保留
  - ⚠️ **审核修正**：改完 `grep -n '"status"' pipeline.py core/cacheManager.py` 确认只剩 docx 顶层三态字段
- [x] 4.5 `core/cacheManager.py` 删 `_normalizeImage`、`_normalizePage`、`_migrateCacheData`；`loadOrCreate` 校验 `version` 且关键字段存在，否则 WARN 重建
  - ⚠️ **审核修正**：先读 `_createNew` 确认 version 字面量，校验条件与之保持一致
- **验证**：`uv run python pipeline.py --help` 无旧参数；`grep -n "enableTranslation\|skip_image_analysis\|reanalyze_images\|enable_translation" pipeline.py` 零命中

## Step 5：文档 + 收尾验证

- [x] 5.1 所有改动文件文件头版本 +0.1、description 写明本次改动
- [x] 5.2 README 重写：新结构图、删 parseFlowApi 独立 CLI 段落、删 legacy 参数、`-o` 改标注可选、补 `core/` 模块表
- [x] 5.3 核对 `.env.example` 与代码实际读取的 8 个变量一致
- [x] 5.4 冒烟验证（不依赖真实 API）：
  - `uv run python -m compileall pipeline.py core/ docxTools/` 全编译通过
  - `uv run python pipeline.py --help` 正常
  - 手工构造 `progress.json` 到临时输出目录，跑 `--tasks image`（无 key → WARN 分支）验证 cache-only 路径不崩
  - 有 docx 样本则跑 `--tasks parse` 验证 docx 链路

## 风险点

1. Step 1 与 Step 2 有耦合（移入 mdPostprocess 的函数不含死代码），严格按顺序执行
2. `core/mdPostprocess` 不 import cacheManager（只操作 dict），无循环依赖
3. pipeline.py 内的延迟 import 路径容易漏改，Step 1 验证时重点检查
4. 顺手清理根目录 `__pycache__/`（旧字节码，gitignore 内，无害）
