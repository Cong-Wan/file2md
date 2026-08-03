# 执行计划审核报告 - plan-md-to-pdf.md v1.2（DOCX 翻译部分）

> 审核范围：v1.2 新增的 DOCX 翻译 + 翻译 PDF 渲染，以及 v1.1 的 12 条审核问题在 v1.2 的延续性。
> 依据文件：docs/plan-md-to-pdf.md(v1.2)、pipeline.py(v3.6)、core/cacheManager.py(v1.5)、
> core/translator.py(v1.7)、core/parseFlowApi.py(v2.8)、core/mdPostprocess.py(v1.0)。

## 总览
- 审核对象：plan-md-to-pdf.md v1.2 第 5.4/5.5 节、R1/R2 风险、任务分解 5/6、v1.1 十二条延续性、v1.2 六个自审补丁
- 发现问题：🟠 2 个 / 🟡 7 个 / 🔵 1 个（低问题已归纳合并）
- 整体评价：v1.2 把 DOCX 翻译纳入本次范围的方向正确，v1.1 的 11 条修复均妥善延续（第 7 条「DOCX 不产 _cn.pdf」属用户决策有意回退，合理）。但 `_runDocx` 现有流程是「解析→image→写文件」单趟模型，v1.2 只改了早返回条件，**没有同步改造解析分支与渲染源回退**，会引入「completed 缓存被重新解析覆盖」「finalMarkdown=None 渲染崩溃」两个高危场景。DOCX 翻译失败兜底、与 resetRunningStates/resetRunningStates 的对称性也存在缺口。

---

## 问题清单

### 🟠【高1】`_runDocx` 早返回放宽后，解析分支未同步改造 —— completed 缓存会被重新解析覆盖

**位置**: plan 5.4（早返回）+ pipeline.py `_runDocx` 解析分支（`if docxData is None or docxData.get("status") not in ("parsed", "analyzing"):`）
**问题**: v1.2 把早返回条件改为「`status=="completed"` 且 `pdf not in tasks and translate not in tasks` 才 return」。放宽后，当 `docx.status=="completed"` 且用户跑 `--tasks translate` 或 `--tasks pdf` 时，不再早返回，继续往下走到解析分支。而解析分支的判定是 `docxData is None or docxData.get("status") not in ("parsed", "analyzing")`——`completed` 不在 `("parsed","analyzing")` 中，条件为真，**会重新调用 `parseDocx` 并 `cache.setDocxRaw` 覆盖整个 docx 字典**。后果：
1. 重新解析浪费时间；
2. `setDocxRaw` 把 `status` 重置为 `"parsed"`、`finalMarkdown` 置 `None`、`images` 全部 `analysisStatus="pending"`——**已完成的图片分析结果全部丢失**；
3. 若旧缓存已有 `translatedContent`（之前翻译过），`setDocxRaw` 重建字典也会把翻译字段抹掉——**译文丢失**。

这是 v1.2 最严重的回退：v1.1 高2（DOCX 缓存命中）在 v1.2 下反而恶化。
**修复方案**: 放宽早返回的同时，解析分支必须改为「仅在无 docx 缓存时解析」，已完成/已解析状态一律走缓存恢复。建议把解析分支条件改为仅 `docxData is None`，并在缓存恢复分支按 `status` 分派：
```python
def _runDocx(...):
    docxData = cache._data.get("docx")
    tasks = args.pipeline_tasks

    # 早返回：已完成且本次无 pdf/translate 需求
    if docxData and docxData.get("status") == "completed" \
       and "pdf" not in tasks and "translate" not in tasks:
        log("DOCX 已完成（缓存），跳过", "INFO", verbose)
        return

    # 仅在无缓存时解析；任何已有缓存状态都不重新解析
    if docxData is None:
        markdown = parseDocx(inputPath, outputDir, outputFileName, verbose)
        imageSources = extractImageSources(markdown, outputDir, verbose)
        images = [...]
        cache.setDocxRaw(markdown, images)
        with open(outputPath, "w", encoding="utf-8") as f:
            f.write(markdown)
    else:
        log(f"从缓存恢复 DOCX（status={docxData.get('status')}），跳过解析", "INFO", verbose)

    # 后续 image / translate / pdf 一律从 cache._data["docx"] 取 rawMarkdown/images/finalMarkdown/translatedContent
```
方案 5.4 文字描述「否则继续走对应流程」过于笼统，必须显式落档这条「completed 不重新解析」的约束。

---

### 🟠【高2】DOCX `finalMarkdown=None` 时渲染 `.pdf` 无回退 —— 崩溃或空 PDF

**位置**: plan 5.4「否则渲染 `docx.finalMarkdown` -> `{stem}.pdf`」
**问题**: `docx.finalMarkdown` 只在 `_runDocx` 跑过 image 分析后才由 `cache.setDocxFinal` 写入；`setDocxRaw` 初始化时为 `None`。当 DOCX 无图片、或未配置 `IMAGE_API_KEY`（不跑 image）时，`docx.finalMarkdown` 恒为 `None`。此时方案 5.4「渲染 `docx.finalMarkdown`」会把 `None` 传给 `markdownToPdf`，导致崩溃或产出空 PDF。任务分解第 6 步验证也只覆盖了 `--tasks translate,pdf`（含 translate）与缓存重渲染，**没有覆盖「DOCX 无 image 分析时渲染 .pdf」**。
**修复方案**: 渲染源选择必须带 rawMarkdown 回退，与 `rebuildMarkdown` 中 `finalMarkdown or rawMarkdown` 的既有约定一致：
```python
if docx.get("translationStatus") == "completed":
    mdText = docx.get("translatedContent")
    pdfPath = os.path.join(outputDir, f"{stem}_cn.pdf")
else:
    mdText = docx.get("finalMarkdown") or docx.get("rawMarkdown")
    pdfPath = os.path.join(outputDir, f"{stem}.pdf")
```
任务 6 增加：(c) 无 image 分析（`--tasks parse,pdf` 且无图片/无 apiKey）时 `x.pdf` 能正常渲染且内容等于 rawMarkdown。

---

### 🟡【中1】DOCX 翻译失败兜底未定义 —— `setDocxTranslation` 只写成功路径

**位置**: plan 5.5（`setDocxTranslation` 只「写 translatedContent/translatedAt，状态置 completed」）+ plan 5.4（翻译后写 `_cn.md`）
**问题**: `translator.translate()` 可能返回 `None`（全部重试失败）。方案未定义此时：`translationStatus` 设什么？`_cn.md` 写什么？对比 PDF 逐页的 `updateTranslationResult` 是「`completed if content is not None else failed`」一并处理，DOCX 的 `setDocxTranslation` 只有成功分支，**失败路径完全缺失**。而 `iterDocxPendingTranslation` 又声明 `translationStatus in (pending,failed)` 会 yield——没有谁把状态写成 `failed`，断点续传的「失败重试」分支永远进不去。
**修复方案**: `setDocxTranslation` 改为接收可能为 `None` 的 content，与 `updateTranslationResult` 对称：
```python
def setDocxTranslation(self, content):
    with self._lock:
        docx = self._data.get("docx")
        if docx is None:
            return
        docx["translatedContent"] = content
        docx["translatedAt"] = datetime.now().isoformat() if content is not None else None
        docx["translationStatus"] = "completed" if content is not None else "failed"
        self.save()
```
`_runDocx` 翻译后：`content` 为 `None` 时不写 `_cn.md`（或写原文+`<!-- [翻译失败] -->` 注释），并 `log ERROR`；用户重跑时 `failed` 会被 `iterDocxPendingTranslation` 重新捡起。

---

### 🟡【中2】`resetRunningStates` 不覆盖 DOCX 翻译状态 —— 设计不对称且为分块翻译埋雷

**位置**: cacheManager.py `resetRunningStates`（只遍历 `pages`）+ plan 5.5（新增 `translationStatus`）
**问题**: `resetRunningStates` 处理 pages 的 `parseStatus/translationStatus/analysisStatus` 的 `running→pending`，**完全不碰 `docx`**。v1.2 DOCX 整体翻译同步执行、不 `markRunning`，所以中断后 `translationStatus` 仍是 `pending`，能被 `iterDocxPendingTranslation` 捡起——当前恰好不崩。但这意味着：
1. DOCX 翻译状态机与 PDF 逐页不对称（PDF 有 `markTranslationRunning`+reset 配对，DOCX 没有）；
2. 方案 R2 已预告「超长文档分块翻译留作后续」，一旦分块引入 `markDocxTranslationRunning`，`resetRunningStates` 漏改就会导致 running 状态卡死、断点续传失效。
方案 5.5 / 自审1 只论证了 `loadOrCreate` 不需升 CACHE_VERSION，**没有说明 DOCX 翻译为何不 markRunning、以及 `resetRunningStates` 何时需要同步扩展**。
**修复方案**: 二选一：
- (推荐) 明确落档约束：「v1.2 DOCX 翻译为单次同步调用，不引入 `markDocxTranslationRunning`；`iterDocxPendingTranslation` 仅需处理 `pending/failed`」。并在 R2 分块方案里标注「引入 running 时必须同步扩展 `resetRunningStates` 覆盖 docx」。
- 若希望对称，现在就给 `resetRunningStates` 加 docx 分支（即便当前没有 running 写入，也保证未来安全）。
```python
# resetRunningStates 内追加
docx = self._data.get("docx")
if docx and docx.get("translationStatus") == "running":
    docx["translationStatus"] = "pending"
    changed = True
```

---

### 🟡【中3】`iterDocxPendingTranslation` 不校验 docx 已解析/有 rawMarkdown —— 健壮性缺口

**位置**: plan 5.5（`iterDocxPendingTranslation`）
**问题**: 方案描述「`docx.translationStatus in (pending,failed)` 时 yield」，但未校验 `docx` 非空、`status` 已解析、`rawMarkdown` 非空。对照 PDF 的 `iterPendingTranslations` 先判 `parseStatus == "completed"` 再判翻译态。若调用方在 docx 未解析时误调（或 docx 为 None），`.get("translationStatus","pending")` 得 `pending` 会 yield 一个无 `rawMarkdown` 的对象，翻译空内容。
**修复方案**: 加前置守卫：
```python
def iterDocxPendingTranslation(self):
    docx = self._data.get("docx")
    if not docx or not docx.get("rawMarkdown"):
        return
    if docx.get("status") not in ("parsed", "analyzing", "completed"):
        return
    if docx.get("translationStatus", "pending") in ("pending", "failed"):
        yield docx
```

---

### 🟡【中4】译文用 rawMarkdown —— 译文 PDF 会缺少图片分析描述，方案未明确告知后果

**位置**: plan 5.4 自审4
**问题**: 自审4 论证「图片分析结果给 .md 加描述，译文翻译正文，两者正交；rawMarkdown 解析完即有可立即翻译」。但图片分析结果（流程图 mermaid、图表说明等）是正文语义的一部分，原文 `.pdf` 用 `finalMarkdown`（含描述）渲染，译文 `_cn.pdf` 用基于 `rawMarkdown` 的译文渲染——**原文 PDF 含图片描述、译文 PDF 不含**，两者内容不对称。方案把「用 rawMarkdown」定为决策但未点明这个后果，验收时易被当成 bug。
**修复方案**: 二选一，并落档：
- (维持 rawMarkdown) 在 5.4 明确：「译文 PDF 不含图片分析描述块；图片引用 `![](images/...)` 原样保留」。任务 5 验证时断言译文 md 中**无** `> **[图像分析` 块（与 rawMarkdown 对称）。
- (改用 finalMarkdown) 翻译源改 `docx.finalMarkdown or docx.rawMarkdown`，译文与原文对称含图片描述；但需评估翻译器对 mermaid/引用块的处理稳定性。
无论哪种，都要把「为何如此」写清楚，避免验收歧义。

---

### 🟡【中5】R2 超长 DOCX 整体翻译几乎必然失败，v1.2 不处理且失败兜底未定义

**位置**: plan R2（自审6）
**问题**: 自审6 识别了体积风险并「留作后续」，方向克制可接受。但：
1. 真实 DOCX（几百页论文/报告）`rawMarkdown` 轻松超 32k tokens，整体翻译**几乎必然失败**——v1.2 对长 DOCX 实际不可用；
2. 「超限失败时 log WARN」之后 `_cn.md` 产出什么未定义（与中1 关联）；
3. `translatedContent` 设计为单个字符串，未来分块翻译要存多块，字段结构不兼容，需再次迁移。
**修复方案**: 至少在 v1.2 做到：
- 翻译前 `log` 文本长度并粗估 tokens（如 `len(rawMarkdown)//2`），超阈值（如 16k tokens）时 `log ERROR` 并明确提示用户「文档过长，v1.2 整体翻译可能失败，建议拆分」，而非静默 WARN 后产出空文件；
- 失败兜底与中1 一并定义；
- 在 R2 注明「分块翻译方案落地时 `translatedContent` 字段需扩展为分块结构，届时应升 CACHE_VERSION 或加迁移」，为未来留出预期。

---

### 🟡【中6】审5 漏「缓存重渲染」场景 —— image-mode=none 后 `_cn.md` 图片引用死链

**位置**: plan 5.4 自审5 + plan 5.2（pdf 在 image-mode 之前）
**问题**: 自审5 称「pdf 渲染在 image-mode 之前」——首次渲染时 `images/` 目录还在，`_cn.pdf` 正常。但：
1. `_applyImageModeToFile(outputPath,...)` 在 image-mode=none 时调 `applyImageMode`，后者 `shutil.rmtree(imagesDir)` 删除整个 `images/` 目录；
2. `_cn.md`（磁盘）保留图片引用 `![](images/...)`，但 `images/` 已删——`_cn.md` 成死链文件；
3. 任务 6 验证 (b)「再跑命中缓存仍能重渲染」此时 `images/` 已不存在，重渲染 `_cn.pdf` 会缺图或 weasyprint 报警告。
该问题 PDF 分支同样存在（`_cn.md` 一样死链），属既有设计；但 v1.2 把 DOCX 也拉进翻译后同样暴露。
**修复方案**: 在自审5 / 任务 6 明确该后果，二选一：
- (维持现状) 落档「image-mode=none 会在渲染后删除 images 目录，磁盘上的 `_cn.md` 图片引用随之失效；重渲染需先恢复图片或重新 parse」。任务 6 验证 (b) 限定在 image-mode=file 下执行。
- (更稳) `_applyImageModeToFile` 对 `_cn.md` 同样应用 image-mode（none 时移除其图片引用），保持 `_cn.md` 自洽。但这与「译文保留图片引用」的 R1 约束冲突，需取舍。

---

### 🟡【中7】任务分解 5/6 漏关键验证场景

**位置**: plan 7（任务 5、6）
**问题**: 集中遗漏：
1. **任务 5 漏「completed 状态补翻译」**——这正是高1 的核心 bug 场景：先 `--tasks parse,image` 跑到 `status=completed`，再 `--tasks translate`，应断言不重新解析、图片分析结果不丢失、译文产出。当前任务 5 只验证「空缓存 translate」。
2. **任务 5 漏翻译失败兜底验证**（与中1 关联）：`translate` 返回 None 时 `_cn.md`/`translationStatus` 行为。
3. **任务 5 漏旧缓存容错验证**：用一个无翻译字段的旧 docx 缓存跑 `--tasks translate`，断言不报 KeyError。
4. **任务 6 漏「无 translate 只渲染 .pdf」**：`--tasks parse,pdf`（无 translate）应只产 `x.pdf` 不产 `x_cn.pdf`。
5. **任务 6 漏「无 image 分析 finalMarkdown=None」渲染回退**（与高2 关联）。
6. **任务 6 漏 image-mode=none + translate,pdf 渲染顺序**验证（与中6 关联）。
7. **断点续传验证手段不明确**：任务 5 写「中断后重跑不再重译」但没给判定方法——应记 `translatedAt` 时间戳，重跑后断言其未变（或 mock translator 断言未被调用）。
8. **断点续传漏 failed 重试 + 跨任务**：`translationStatus=failed` 重跑能重试；`translate` 完成后单独跑 `--tasks pdf` 应复用缓存译文渲染 `_cn.pdf` 不重译。
**修复方案**: 任务 5/6 补充上述验证项；断点续传用 `translatedAt` 不变 + mock 断言双重确认。

---

### 🔵【低】若干设计与论证细节（归纳）

1. **「已翻译」判定需明确**：5.4「若 DOCX 已翻译」应写明判定为 `translationStatus == "completed"`；`failed` 时回退渲染 `.pdf` 并 `log WARN`（与高2 的回退链联动）。
2. **「翻译在 image 之后」理由不充分**：既然用 rawMarkdown（与 image 结果正交），翻译本可提前或与 image 并行；放后面仅保证 `_cn.md` 与 `.md` 图片引用同源，不影响正确性，可保留但不必以「须在之后」强约束落档。
3. **审2 实现细节**：`iterDocxPendingTranslation` 内部读取 `translationStatus` 也须用 `.get("translationStatus","pending")`，方案文字虽提但实现易漏，需在任务 5 用旧缓存验证兜底（已并入中7）。
4. **审6 需长文档实测**：任务 5 应至少用一篇中等长度 DOCX 实测翻译成功率，记录失败行为，验证 R2 兜底（已并入中5）。

---

## v1.1 十二条延续性核查

| # | v1.1 主题 | v1.2 状态 | 说明 |
|---|-----------|-----------|------|
| 高1 | 三分支 pdf 派发 | ✅ 延续+扩展 | 5.2 保留三分支；5.4 补 DOCX pdf 渲染 |
| 高2 | DOCX 缓存命中 | ⚠️ 恶化 | 早返回条件放宽但解析分支未改，见【高1】 |
| 高3 | base_url=outputDir | ✅ 延续 | 5.1/5.4 均 outputDir |
| 中4 | prompt 约束图片引用 | ✅ 延续+扩展 | R1 扩展到 DOCX，任务3 双验 |
| 中5 | 内存 md+image-mode 前渲染 | ✅ 延续 | 5.2/5.4 均内存渲染在 image-mode 前 |
| 中6 | 公式预处理排除代码块 | ✅ 延续 | 5.1 步骤2 保留 stripCodeSpans |
| 中7 | DOCX 不产 _cn.pdf | 🔄 有意回退 | 用户选 Y，Q5 已解决，v1.2 DOCX 产 _cn.pdf，合理 |
| 中8 | latex2mathml+MathML 评估 | ✅ 延续 | 4.1 保留，任务1 demo 双路对比 |
| 低9 | 任务分解补 DOCX 缓存命中 | ✅ 延续 | 任务5/6 含缓存命中 |
| 低10 | v1 不加 --pdf-target | ✅ 延续 | 5.3 保留 |
| 低11 | --- 转分页符 | ✅ 延续 | 5.1 步骤1 保留（DOCX 译文无---，对 DOCX 不生效，合理） |
| 低12 | 去 md_in_html | ✅ 延续 | 5.1 步骤3 保留 |

**结论**：11 条妥善延续；第 7 条属用户决策有意回退，不视为回退缺陷。无遗漏回退。

---

## v1.2 六个自审补丁核查

| 自审 | 主题 | 到位度 | 缺口 |
|------|------|--------|------|
| 审1 | CACHE_VERSION 不升 | ⚠️ 部分 | 只论证 loadOrCreate 校验，漏 resetRunningStates 不覆盖 docx（见【中2】） |
| 审2 | 旧缓存 .get 容错 | ✅ 基本到位 | 实现时 iterDocxPendingTranslation 内部须 .get（见【低】3） |
| 审3 | DOCX 翻译断点续传 | ⚠️ 部分 | 逻辑正确但依赖【高1】修复；失败状态写入未定义（见【中1】） |
| 审4 | rawMarkdown 翻译理由 | ⚠️ 部分 | 论证成立但未点明译文 PDF 缺图片描述的后果（见【中4】） |
| 审5 | DOCX 与 image-mode 关系 | ⚠️ 部分 | 只考虑首次渲染，漏缓存重渲染死链（见【中6】） |
| 审6 | 整体翻译体积风险 R2 | ⚠️ 部分 | 识别到位但处理不足，失败兜底未定义（见【中5】） |

**结论**：6 个自审补丁方向均正确，但 5 个存在关联缺口，需在落档时补全论证与兜底。

---

## 修复优先级建议
1. 🟠【高1】`_runDocx` 解析分支改造 —— 不修则 v1.2 的 DOCX 翻译/重渲染会覆盖缓存、丢失图片分析与译文，v1.1 高2 反向恶化，必须最先处理。
2. 🟠【高2】finalMarkdown=None 回退 —— 不修则无图片 DOCX 渲染 .pdf 直接崩，任务6 验证覆盖不到。
3. 🟡【中1】翻译失败兜底 +【中2】resetRunningStates 对称性 —— 决定断点续传是否真正可用，且为 R2 分块翻译铺路。
4. 🟡【中7】任务分解补全验证场景 —— 补「completed 补翻译」「失败兜底」「finalMarkdown=None」三个关键验证，否则高1/高2/中1 无法在验收阶段捕获。
