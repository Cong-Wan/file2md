# 方案：Markdown -> PDF 还原任务（pipeline `--tasks pdf`）

> 作者: wilbur  日期: 2026-08-03
> 版本: 1.6
> 变更:
> - v1.1: 根据 pi 审核(12条)修复：补全三分支 pdf 派发(高1)、DOCX 缓存命中(高2)、
>   base_url 改 outputDir(高3)、translator prompt 硬约束图片引用(中4)、内存 md+image-mode 前渲染(中5)、
>   公式预处理排除代码块(中6)、声明 DOCX 不产 _cn.pdf(中7)、公式增评 latex2mathml+MathML(中8)、
>   任务分解补 DOCX 缓存命中(低9)、v1 不加 --pdf-target(低10)、--- 转分页符(低11)、去 md_in_html(低12)。
> - v1.2: 用户选 Y，把「DOCX 翻译 + 翻译 PDF」一并纳入本次范围。DOCX 走单文档整体翻译（非逐页），
>   cacheManager.docx 加翻译字段，DOCX pdf 渲染支持 _cn.pdf。
>   v1.2 自审补丁（pi 审核超时，本会话自审）：旧缓存 .get 容错(审2)、DOCX 翻译断点续传(审3)、
>   翻译用 rawMarkdown 的理由(审4)、DOCX 与 image-mode 关系(审5)、DOCX 整体翻译体积风险 R2(审6)。
> - v1.3: 子代理审查（docs/codeReview/260803_docxTranslationPlanReview.md）发现 2高7中，修复：
>   _runDocx 解析分支仅无缓存时解析(高1)、渲染源 finalMarkdown or rawMarkdown 回退(高2)、
>   setDocxTranslation 失败兜底(中1)、resetRunningStates 加 docx 分支(中2)、iterDocxPendingTranslation 前置守卫(中3)、
>   译文用 rawMarkdown 后果明示(中4)、R2 翻译前预估长度+失败兜底(中5)、image-mode=none 重渲染死链后果(中6)、
>   任务分解补全验证场景(中7)。
> - v1.5: 修正审题错误（用户指出：PDF 输入本身已有原 PDF，pdf 任务只需产 `_cn.pdf`）。
>   PDF/DOCX 分支 pdf 任务统一**只产译文 `_cn.pdf`**，无翻译时跳过不产任何 PDF；
>   移除原文 .pdf 渲染分支与 6(a)/6(c)/6(d) 中的原文 .pdf 验证点；高2 回退链保留文档备查但不被 pdf 任务触发；Q1 解决。
> - v1.6: 四审发现 3 处文档级残留矛盾，补丁修复：5.3「否则 .pdf」改跳过(矛盾1)、
>   5.4 删原文.pdf不对称表述+高2回退链标注不实现(矛盾2)、5.2 删validate_tasks前置校验保留_runPdfTask运行时WARN(矛盾3)。五审确认可进入实施。

## 1. 目标
在 pipeline 中新增 `pdf` 任务，将解析/翻译产出的 Markdown 渲染为 PDF，形成「原 PDF + 翻译 PDF」配对。
要求保留**语义结构**：标题层级、段落顺序、图片与正文的相对位置、表格、公式、代码块。

## 2. 已确定决策（来自用户）
- 还原模式：**选项 B（reflowable 语义结构还原）**，不做像素级 bbox 还原。
- 支持范围：PDF 解析分支 + DOCX 解析分支都要支持。
- 形式：新增 `--tasks pdf`。
- **DOCX 翻译（v1.2 新增）**：用户选 Y，DOCX 也支持翻译 + 翻译 PDF。
  DOCX 是单文档，走整体翻译（`translator.translate(rawMarkdown)`），非逐页。

## 3. 现状分析（关键结论）
- MinerU 解析每个 `ContentBlock` 带 `bbox`(归一化坐标)，但**选项 B 不需要 bbox**--
  现有 md 输出（`finalMarkdown` / `_cn.md`）已含正确的标题层级、图片内联引用、表格 HTML、`$$...$$` 公式。
  -> **无需改 cache 结构**。
- pipeline tasks 现为 `parse/image/translate`；新增 `pdf`。
- **关键缺口（审核高1）**：`_runPdfOrImage` 三分支与 `_runDocx` 目前**都没调用** pdf 渲染，
  必须显式补 pdf 派发（详见 5.2）。
- **关键缺口（审核高2）**：`_runDocx` 在 `docx.status=="completed"` 时直接 `return`，
  二次跑 `--tasks pdf` 会跳过渲染，必须特殊处理（详见 5.4）。
- 当前环境**没有任何 md->pdf 库**（weasyprint/reportlab/fpdf2/markdown 均未安装），也无 pandoc/wkhtmltopdf 二进制。

## 4. 渲染引擎选型（待确认，默认 A）
MinerU md 含：标题、段落、图片、HTML 表格、`$$...$$` LaTeX 公式、代码块、中文(CJK)。

| 选项 | 栈 | 优点 | 缺点 |
|------|----|----|------|
| **A(推荐)** | `markdown`(lib) + `weasyprint` | CSS/CJK/表格最佳，代码最少 | 需 `brew install pango` |
| B | `xhtml2pdf` | 纯 Python 无系统依赖 | CJK 需注册字体 |
| C | `fpdf2` | 轻量、CJK 好 | 需手写 markdown 解析，代码量大 |

### 4.1 公式渲染（审核中8 增强）
- **主路径（新增评估）**：`latex2mathml` 把 `$$...$$` 转 MathML，weasyprint 原生渲染（矢量、无系统依赖）。
  在任务分解第 1 步 demo 中对比 matplotlib PNG 与 latex2mathml+MathML 两条路，对 `align/cases/matrix` 等
  复杂公式效果后定方案。
- **回退**：matplotlib mathtext 渲染 PNG；不支持的宏回退为等宽文本。
- 两条路都在任务1 demo 验证后二选一，避免 mathtext 对论文公式回退过弱。

## 5. 实现计划
### 5.1 新模块 `core/mdToPdf.py`
`markdownToPdf(mdText, pdfPath, imageBaseDir, verbose)`:

1. **分页符转换（审核低11）**：`mdText.replace("\n\n---\n\n", "\n\n<div style=\"page-break-after:always\"></div>\n\n")`
   ——把 rebuild 的每页分隔 `---` 转成真正分页符，避免 PDF 中出现水平线。仅作用于 pdf 输入，不回写 .md。
2. **公式预处理（审核中6）**：先 `stripCodeSpans(md)` 切出 ``` 代码块与 `` 行内代码（用占位符替换），
   只对非代码段扫描 `$$...$$`/`$...$` 渲染为 `<img>`/MathML，最后还原代码段。避免误伤 `$VAR`、`$x` 等。
3. **HTML 转换**：`markdown` lib，开启 `tables` + `fenced_code` 扩展（**去掉 `md_in_html`**，审核低12）。
4. **套模板 + CSS**：CJK 字体、标题层级、表格边框、图片居中、代码等宽字体。
5. **写 PDF（审核高3）**：`weasyprint.HTML(string=html, base_url=imageBaseDir).write_pdf(pdfPath)`
   ——`base_url` 传 **outputDir 绝对路径**（不是 `dirname(mdPath)`），保证 `images/xxx.png` 解析正确。
   DOCX 与 PDF 共用：其图片也是 `images/xxx.png` 相对 outputDir，同样传 outputDir。

纯函数，不依赖 pipeline 内部状态，可独立测试。

### 5.2 pipeline 接入 -- PDF 分支（审核高1；v1.4 中新2 明确判定标准；v1.5 只产 _cn.pdf）
- `VALID_PIPELINE_TASKS` 末尾追加 `"pdf"`；`DEFAULT_PIPELINE_TASKS` 不变（默认不含 pdf）。
- `parse_tasks`/`validate_tasks` 自动兼容。
- **pdf 任务定位（v1.5）**：PDF 输入本身已有原 PDF，pdf 任务**只产 `_cn.pdf`（译文 PDF）**，不产原文 `.pdf`。
  无翻译时 pdf 任务无产物 -> 由 `_runPdfTask` 运行时判定（见下），**不在 `validate_tasks` 做前置校验**
  （v1.6 矛盾3：validate_tasks 在 cache.loadOrCreate 之前调用，无法查缓存译文；且其契约是 raise+exit，
  与 WARN+跳过语义冲突，会阻断任务6(c) 的 parse）。
- 新增 `_runPdfTask(ctx)`：
  - 前置：`_requireParsedCache(ctx, "pdf")`。
  - **「translate 已完成」判定（v1.4 中新2）**：cache 中存在**任意已解析页的 `translationStatus=="completed"`**则视为已翻译（按页聚合判定）。
  - 已翻译 -> `mdText = ctx.cache.rebuildTranslationMarkdown()`，目标 `{stem}_cn.pdf`；
  - 未翻译 -> `log WARN`「无译文，pdf 任务跳过」，`return`（**不产原文 .pdf**，原 PDF 已是输入）。
    ⚠️ 不用 `ctx.cnOutputPath is not None` 判定（它只在 `translate in tasks` 时非 None，单独跑 `--tasks pdf` 会误判）。
  - `markdownToPdf(mdText, pdfPath, ctx.outputDir, verbose)`。
- **`_runPdfOrImage` 三分支补 pdf 派发（审核高1）**：
  - `if "parse" in tasks:` parse 跑完后，`if "pdf" in tasks: _runPdfTask(ctx)`；
  - `elif len(tasks) > 1:` `_runCacheOnlyPostprocessors` 后，`if "pdf" in tasks: _runPdfTask(ctx)`；
  - `else:` 单 task 时 `if "pdf" in tasks: _runPdfTask(ctx)`。
- **执行顺序（审核中5）**：`_runPdfTask` 必须在 `_applyImageModeToFile` **之前**调用。
  这样 `--image-mode none --tasks translate,pdf` 可同时得到「无图 .md」+「有图 `_cn.pdf`」。
  即 `_runPdfOrImage` 末尾的 `_applyImageModeToFile` 放在 pdf 渲染之后。

### 5.3 CLI（审核低10）
- v1 **不加** `--pdf-target`。默认：translate 完成渲染 `_cn.pdf`，否则 pdf 任务跳过不产 PDF（v1.6 矛盾1 修正）。
- `--tasks pdf` 已自动可用，无新参数。

### 5.4 DOCX 分支接入（审核高2 + 中7；v1.2 扩展翻译；v1.3 修复高1/高2/中1/中3/中4/中6；v1.4 中新3 跳过 image 分支）
- **早返回（v1.3 高1）**：`_runDocx` 开头，若 `docx.status=="completed"` 且 **`"pdf" not in tasks and "translate" not in tasks`** 才早返回。
- **解析分支（v1.3 高1 关键修复）**：**仅在 `docxData is None` 时调 `parseDocx` + `setDocxRaw`**；
  任何已有缓存状态（parsed/analyzing/completed）一律走缓存恢复分支（log「从缓存恢复 status=...」）。
  -> 避免放宽早返回后 completed 缓存被重新解析覆盖（丢图片分析/译文）。
- **image 分支跳过（v1.4 中新3）**：completed 状态下，若 **`"image" not in tasks`**，跳过 image 分析分支，
  直接复用缓存 `finalMarkdown`。避免 `--tasks translate` 副作用重跑 image 并重写 .md（幂等但语义不符，
  且 `--image-mode none` 会触发 rmtree）。
- **DOCX pdf 渲染须在 `_applyImageModeToFile` 之前（v1.4 低新1，与 5.2 对齐）**。
- **DOCX 翻译（v1.2 新增；v1.3 中1 失败兜底 + 中3 守卫 + 中4 后果 + 中5 长度预估；v1.4 低新2 统一不写 _cn.md）**：
  - `if "translate" in tasks and translator:` 时，先 `iterDocxPendingTranslation()` 查是否需翻译（含前置守卫）；
  - **长度预估（中5；v1.4 低新3 中文系数）**：翻译前 `log` rawMarkdown 长度，中文文档粗估 tokens 用 `len(rawMarkdown)` 或 `len*1.5`（中文 1 字符≈1-2 tokens，非 0.5），
    超阈值（如 16k tokens）时 `log ERROR` 明示「文档过长，v1.2 整体翻译可能失败，建议拆分」，非静默；
  - 调 `translator.translate(rawMarkdown)` 得 content（**可能为 None 或空串**）；
  - `cache.setDocxTranslation(content)`（v1.3 中1：content 为 None 或空串时状态置 `failed`，空串视同 None，与 PDF `updateTranslationResult` 对称）；
  - content 为 None/空串时：**不写 `_cn.md`**（v1.4 低新2 删除「或写原文+注释」歧义，统一为不写），`log ERROR`；
    重跑时 `failed` 被 `iterDocxPendingTranslation` 重新捡起重试。
  - **断点续传**：`translationStatus=="completed"` 时跳过翻译、复用缓存译文重写 `_cn.md`（与 PDF 对称）。
  - **译文用 rawMarkdown（中4 后果明示）**：译文 PDF **不含图片分析描述块**（mermaid/图表说明等），
    但图片引用 `![](images/...)` 原样保留。译文用 rawMarkdown，故译文 md/`_cn.pdf` 无 `> **[图像分析` 块，
    任务5验证时断言（v1.6 矛盾2 修正：删去「原文 .pdf 用 finalMarkdown…不对称」表述，v1.5 不产原文 .pdf）。
- **DOCX pdf 渲染（v1.2 扩展；v1.3 高2 回退 + 中6 死链明示；v1.5 只产 _cn.pdf）**：
  - **pdf 任务定位（v1.5）**：与 PDF 分支一致，**只产 `_cn.pdf`（译文 PDF）**，不产原文 `.pdf`。
    理由：pdf 任务初衷是「翻译好的 md 转 pdf」，DOCX 原文已是 docx，用户要的是译文 PDF。
  - 若 `translationStatus=="completed"` -> 渲染 `docx.translatedContent` -> `{stem}_cn.pdf`；
  - 否则（pending/failed）-> `log WARN`「无译文，pdf 任务跳过」，`return`（**不产原文 .pdf**）。
  - 渲染源用内存 md，base_url=outputDir。
  - **image-mode=none 重渲染死链（中6 明示）**：image-mode=none 会在渲染后 `shutil.rmtree(images/)`，
    磁盘 `_cn.md` 图片引用随之失效。**任务6验证(b)重渲染限定在 image-mode=file 下执行**；
    重渲染场景需先恢复图片或重新 parse（既有设计，PDF 分支同样存在）。
  - **注（v1.6 矛盾2补澄清）**：高2 的 `finalMarkdown or rawMarkdown` 回退链仅用于「原文 .pdf 渲染」场景，
    v1.5 后 pdf 任务不产原文 .pdf，**该回退链不实现、仅作历史决策记录**，避免实现者困惑。

### 5.5 cacheManager 扩展（v1.2 新增 DOCX 翻译字段；v1.3 修复中1/中2/中3）
- `setDocxRaw` 的 docx 结构新增：
  `translatedContent: None`、`translationStatus: "pending"`、`translatedAt: None`。
- **`setDocxTranslation(content)`（v1.3 中1 失败兜底；v1.4 中新1 补锁+空串视同 None）**：接收可能为 None 或空串的 content，与 PDF `updateTranslationResult` 对称：
  ```python
  def setDocxTranslation(self, content):
      with self._lock:                       # v1.4 中新1：补锁，与类约定一致
          docx = self._data.get("docx")
          if docx is None: return
          ok = bool(content and content.strip())  # 空串视同 None
          docx["translatedContent"] = content if ok else None
          docx["translatedAt"] = datetime.now().isoformat() if ok else None
          docx["translationStatus"] = "completed" if ok else "failed"
          self.save()
  ```
- **`iterDocxPendingTranslation()`（v1.3 中3 前置守卫）**：
  ```python
  def iterDocxPendingTranslation(self):
      docx = self._data.get("docx")
      if not docx or not docx.get("rawMarkdown"): return
      if docx.get("status") not in ("parsed","analyzing","completed"): return
      if docx.get("translationStatus","pending") in ("pending","failed"):
          yield docx
  ```
- **`resetRunningStates` 扩展 docx 分支（v1.3 中2 对称性）**：
  ```python
  docx = self._data.get("docx")
  if docx and docx.get("translationStatus") == "running":
      docx["translationStatus"] = "pending"
      changed = True
  ```
  -> 即便 v1.2 DOCX 翻译不 markRunning（单次同步），也保证未来 R2 分块翻译引入 running 时不会卡死断点续传。
- CACHE_VERSION 不升（顶层字段校验不含 docx 内部，安全）。
- **旧缓存容错**：读取翻译字段一律用 `.get("translatedContent")`、`.get("translationStatus","pending")`，避免旧缓存 KeyError。

## 6. 风险点 / 假设
- **R1（审核中4，已加固，PDF 与 DOCX 均适用）**：translator system prompt 现 6 条规则**无一条**约束图片引用。
  实现 `translate,pdf` 前，先在 `core/translator.py` 的 `TRANSLATION_SYSTEM_PROMPT` 追加：
  `"7. 图片引用 ![](路径) 必须原样保留，不得删除、改写路径或去掉感叹号；alt 文本可翻译但路径不变\n"`
  并在任务3验证时断言 `_cn.md` 中 `![](...)` 数量与原文一致（PDF 与 DOCX 皆验）。
- **R2（自审6 + v1.3 中5）**：DOCX 单文档整体翻译，`rawMarkdown` 可能很长，超翻译 API 单次上下文。
  v1.3 处理：翻译前预估 tokens，超阈值（如 16k）`log ERROR` 明示「文档过长，v1.2 整体翻译可能失败，建议拆分」；
  失败走 `setDocxTranslation(None)`（状态 failed）兜底，重跑可重试。
  **分块翻译留作后续**：落地时 `translatedContent` 字段需扩展为分块结构，届时应升 CACHE_VERSION 或加迁移，
  并引入 `markDocxTranslationRunning`（v1.3 已扩展 resetRunningStates 覆盖 docx 为其铺路）。
- R3：weasyprint 的 `pango` 系统依赖在 macOS 需 `brew install pango`（一次性）。
- R4：复杂公式回退--优先 latex2mathml+MathML（矢量），其次 matplotlib PNG，最后等宽文本。
- R5：DOCX 与 PDF 共用渲染路径，图片均为 `images/xxx.png` 相对 outputDir。
- **R6（v1.3 中6）**：image-mode=none 渲染后会删 `images/` 目录，磁盘 `_cn.md` 图片引用成死链。
  重渲染需先恢复图片或重新 parse（既有设计，PDF 分支同样存在）。任务6验证(b)限定 image-mode=file。

## 7. 任务分解与验证
1. ✅ **选型确认 + 装依赖 + demo**（v1.7 完成）：`uv add xhtml2pdf markdown matplotlib`（weasyprint 弃用）；
   demo `scripts/demoMd2Pdf.py` 验证通过：中文CJK✓/表格✓/图片✓(link_callback)/公式PNG✓/分页✓/代码块(含中文修复)✓/align回退文本✓。
2. ✅ 写 `core/mdToPdf.py`（v1.1：stripCodeSpans、---转分页、link_callback、4种公式格式、sanitizeTables空单元格）->
   验证：Llama3 前300行渲染17页，29图片对象，表格不崩，公式PNG 26个。
3. ✅ **translator prompt 加固（R1）** -> 验证：翻译含图片 md，2个引用数不变、路径一致、alt被译。
4. ✅ pipeline PDF 分支接入 -> 验证：`--tasks parse,translate,pdf` 产 `{stem}_cn.pdf`（109KB中文+图）；
   `--tasks pdf` 跨任务复用缓存译文不重译；无译文 WARN 跳过不产 PDF。
5. ✅ cacheManager 扩展 DOCX 翻译字段 + `_runDocx` 接翻译 ->
   验证：
   - (a) 空缓存 `--tasks translate` 产出 `x_cn.md`，✓；
   - (b) completed 补翻译不重解析 ✓；
   - (c) 翻译失败兜底 None/空串->failed ✓（单元）；
   - (d) 旧缓存容错不 KeyError ✓（单元）；
   - (e) 断点续传不重译 ✓；
   - (f) 译文无图片描述块（DOCX无图场景OK）。
6. ✅ DOCX pdf 渲染接入（与任务5同在 _runDocx 实现）->
   验证：
   - (a) 空缓存 `--tasks translate,pdf` 产出 `x_cn.pdf`（53KB）✓；
   - (b) 缓存命中重渲染（image-mode=file 场景OK）；
   - (c) 无 translate 不产 PDF：`--tasks parse,pdf` WARN 跳过 ✓；
   - (d) 翻译失败不产 PDF（与5c关联，failed 时 pdf 跳过）✓；
   - (e) 跨任务 `--tasks pdf` 复用缓存译文渲染 `_cn.pdf` ✓；
   - (f)(g) image-mode=none渲染顺序/中等长度DOCX实测：留作后续（当前测试DOCX规模小）。

## 8. 待确认问题
- ~~Q1（已解决，v1.5）~~：渲染目标 = **只产 `_cn.pdf`**。PDF 输入已有原 PDF、DOCX 输入原文是 docx，pdf 任务初衷是「译文 md 转 pdf」，故只产译文 PDF，无翻译时 pdf 任务跳过。
- Q2：引擎选型 A/B/C？（默认 A: weasyprint）
- Q3：公式优先 latex2mathml+MathML，复杂宏回退等宽文本可接受？（默认接受）
- Q4：DOCX 与 PDF 共用渲染路径 OK？（默认 OK）
- ~~Q5（已解决，用户选 Y）~~：DOCX 翻译纳入本次范围，DOCX 也产 `_cn.pdf`。
