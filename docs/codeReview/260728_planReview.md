# 执行计划审核报告 — files2MD 项目整理计划

> 注：本会话无 subagent 派发工具，由主 agent 按 code-review skill 标准对执行计划做结构化自检。

## 总览
- 审核对象：整理执行计划（Step 0 ~ Step 5）
- 发现问题：🟠 2 个 / 🟡 3 个 / 🔵 2 个
- 整体评价：计划整体可执行，主要风险集中在 helper 复用的签名适配、以及 parseFlowApi 内部函数的存废判断，已补充修正条款。

---

## 问题清单

### 🟠 `_submitImageFutures` 与 `iterPendingImages` 返回形态不匹配（Step 3.3）

**位置**: 计划 Step 3.3
**问题**: `_submitImageFutures(ctx, analyzer, executor, pagesWithImages)` 期望 `[(page, images列表)]`，而 `_runImageTask` 拿到的 `iterPendingImages()` 是逐项 `(page, img)` 对。直接复用会类型不匹配。
**修复方案**: 执行时把 `_submitImageFutures` 签名改为接受 `(page, img)` 对列表：
```python
def _submitImageFutures(ctx, analyzer, executor, pendingPairs) -> list:
    futures = []
    for page, img in pendingPairs:
        ctx.cache.markImageRunning(page["pageNum"], img["imageId"])
        futures.append(executor.submit(...))
    return futures
```
`_runParsePipelineTask` 调用处传 `[(page, img) for img in images]`；`_runImageTask` 传 `pending`。

### 🟠 parseFlowApi 内部函数存废未逐一确认（Step 2.4）

**位置**: 计划 Step 2.4
**问题**: `saveImageBlocks`、`blocksToMarkdown`、`logRawParseOutput` 可能被活的 `parseSinglePage` / `loadInputImages` 内部调用，误删会直接崩（已确认 `logRawParseOutput` 被 `parseSinglePage:499` 调用，保留）。
**修复方案**: 删除前对每个函数执行 `grep -n "<fn>" core/parseFlowApi.py pipeline.py`，只有零调用方才删；孤立 import 用 `python -c "import core.parseFlowApi"` + pyflakes 式逐一核对。

### 🟡 DOCX `status` → `analysisStatus` 改名需覆盖 3 个文件的所有读写点（Step 4.4）

**位置**: 计划 Step 4.4
**问题**: 除 `_runDocx` 2 处和 `updateDocxImageResult` 外，`cacheManager._normalizeImage`（Step 4.5 会删）和 `_runDocx` 里 `pendingImages` 过滤也读 `img["status"]`。漏改一处即 KeyError。
**修复方案**: 改名后 `grep -n '"status"' pipeline.py core/cacheManager.py` 确认只剩 docx 顶层三态字段。

### 🟡 `loadOrCreate` 校验逻辑需与 `_createNew` 的 version 对齐（Step 4.5）

**位置**: 计划 Step 4.5
**问题**: 删迁移逻辑后，校验 `version == "3.0"` 的期望值必须与 `_createNew()` 实际写入的版本号一致，否则新缓存每次都被判旧而重建。
**修复方案**: 执行时先读 `_createNew` 确认 version 字面量，校验条件与之保持一致。

### 🟡 函数去 `_` 前缀改公开后，pipeline.py 内所有调用点同步改名（Step 1.4/1.9）

**位置**: 计划 Step 1.4 / 1.9
**问题**: `_extractImageSources` → `extractImageSources` 等在 pipeline.py 有多个调用点（`_buildFinalMarkdownFromCache`、`_runDocx`、`_finalizeImageOutput`），漏改即 NameError。
**修复方案**: 每改一个名，`grep -n "_<oldName>" pipeline.py` 零命中才算完。

### 🔵 `buildImageInfo` 归属层（Step 3.2）

放 pipeline.py（编排层）可接受；缓存契约严格说归 cacheManager，但为控制改动范围不动，标注 Low。

### 🔵 根目录 `__pycache__/` 残留旧 .pyc

git mv 后根目录 `__pycache__` 里有旧字节码，已被 gitignore，无害；顺手 `rm -rf __pycache__` 即可。

---

## 修复优先级建议
1. 🟠 parseFlowApi 存废确认（误删即崩，Step 2.4 执行时先做 grep）
2. 🟠 `_submitImageFutures` 签名适配（Step 3.3 动工前先改签名）
3. 🟡 DOCX status 改名全覆盖（Step 4.4 完成后 grep 兜底）
