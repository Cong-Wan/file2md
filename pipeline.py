'''
Author: wilbur
Version: 3.2
  Date: 2026-07-28
  Description: 重构 Step1/2：模块迁入 core 包，日志/MD 后处理函数改为 core.logUtils/core.mdPostprocess 公开实现；
               删除 9 个死函数（parsePdfOrImage、_stripOldAnalysisBlocks、_translateAndWrite、processImageAnalysis、
               _parsePdfPages、_parseAndDispatchLoop、_finalizePdfPipeline、_shutdownExecutors、_runReanalyze）

Version: 3.1
  Date: 2026-05-21
  Description: 改为按页提交解析后处理任务，支持 cache-only 后处理并行

Version: 3.0
  Date: 2026-04-25
  Description: 所有 API 配置统一从 .env 读取，移除 API 相关 CLI 参数

Version: 2.9
  Date: 2026-04-24
  Description: 重构异步流水线 —— 主线程纯生产者 + OrderedMarkdownWriter + worker 自管异常，
               删除旧 drainAndWriteCn、activeTranslations 滑动窗口、_rebuildCnMarkdown

Version: 2.8
  Date: 2026-04-24
  Description: 修复翻译提交无反压 bug：实现滑动窗口控制并发提交，活跃翻译不超过 maxConcurrent

Version: 2.7
  Date: 2026-04-24
  Description: 修复翻译三个 bug：1) 未启用翻译时不创建 _cn.md；2) 翻译不再阻塞解析；3) 翻译结果增量写入 _cn.md

Version: 2.4
  Date: 2026-04-24
  Description: 集成翻译流程到 pipeline，支持并发翻译、断点续传、生成 _cn.md

Version: 2.2
  Date: 2026-04-23
  Description: 修复断点续传对图片分析无效的 bug；
               修复图片分析结果未写回 .md 文件的 bug

Version: 2.1
  Date: 2026-04-23
  Description: 修复图片分析同步阻塞问题：图片分析改为异步提交模式，解析与图片分析解耦

Version: 1.3
  Date: 2026-04-21
  Description: 统一 MD 写入点；-o 改为可选自动创建；图片目录统一为 images；
               输出文件名统一为 {stem}.md；重试策略改为 10 次/15 秒固定间隔

Version: 1.2
  Date: 2026-04-14
  Description: 修复 base64 图片分析结果丢失问题：新增索引匹配模式；
               线程池关闭增加 try/except 保护

Version: 1.0
  Date: 2026-04-13
  Description: 统一数据处理管道 CLI，支持 PDF/DOCX/Image → Markdown + 图像理解
'''

import os
import sys
import re
import time
import argparse
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv

load_dotenv()

from core.imageAnalyzer import ImageAnalyzer
from core.cacheManager import CacheManager
from core.logUtils import log, logSeparator
from core.mdPostprocess import (
    applyImageMode,
    buildFinalMarkdownFromCache,
    extractImageSources,
)


# ============================================================
# 支持的文件类型
# ============================================================

SUPPORTED_EXTENSIONS = {
    ".pdf": "pdf",
    ".docx": "docx",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".bmp": "image",
    ".tiff": "image",
    ".tif": "image",
    ".webp": "image",
}

VALID_PIPELINE_TASKS = ("parse", "image", "translate")
DEFAULT_PIPELINE_TASKS = ("parse", "image")


def parse_tasks(args) -> tuple[str, ...]:
    """Resolve explicit --tasks or legacy flags into an ordered task tuple."""
    rawTasks = getattr(args, "tasks", "") or ""
    if rawTasks.strip():
        requested = [part.strip().lower() for part in rawTasks.split(",") if part.strip()]
    elif getattr(args, "reanalyze_images", False):
        requested = ["image"]
    elif getattr(args, "skip_image_analysis", False):
        requested = ["parse"]
    else:
        requested = list(DEFAULT_PIPELINE_TASKS)
        if getattr(args, "enable_translation", False):
            requested.append("translate")

    unknown = [task for task in requested if task not in VALID_PIPELINE_TASKS]
    if unknown:
        raise ValueError(f"不支持的任务: {', '.join(unknown)}；支持: {', '.join(VALID_PIPELINE_TASKS)}")

    resolved = []
    for task in VALID_PIPELINE_TASKS:
        if task in requested and task not in resolved:
            resolved.append(task)
    if not resolved:
        raise ValueError("--tasks 不能为空；支持: parse,image,translate")
    return tuple(resolved)


def validate_tasks(tasks: tuple[str, ...], clean_cache: bool) -> None:
    """Validate task combinations that can be checked before cache loading."""
    if clean_cache and "parse" not in tasks:
        joined = ",".join(tasks)
        raise ValueError(f"--tasks {joined} 不能和 --clean-cache 同用；clean-cache 后没有可后处理的解析缓存")


# ============================================================
# Step 1: 文件类型判断
# ============================================================

def detectFileType(inputPath: str) -> str:
    """根据扩展名判断文件类型。

    返回: 'docx' | 'pdf' | 'image'
    异常: 遇到不支持的类型直接 sys.exit(1)
    """
    suffix = Path(inputPath).suffix.lower()
    fileType = SUPPORTED_EXTENSIONS.get(suffix)
    if fileType is None:
        log(f"不支持的文件格式: {suffix}，支持: {', '.join(SUPPORTED_EXTENSIONS.keys())}", "ERROR", True)
        sys.exit(1)
    return fileType


# ============================================================
# Step 1a: DOCX 解析
# ============================================================

def parseDocx(inputPath: str, outputDir: str, outputFileName: str, verbose: bool) -> str:
    """调用 docxTools 模块解析 DOCX 文件。

    参数:
        inputPath: DOCX 文件路径
        outputDir: 输出目录
        outputFileName: 输出 MD 文件名（如 report.md）

    返回:
        Markdown 文本

    注意: convert_file() 会将 MD 作为副作用写入磁盘（用于触发图片提取）。
    pipeline 最终会再次覆盖该文件（包含图像分析结果）。
    """
    logSeparator("DOCX 解析", verbose)
    from docxTools import DocxToMarkdownConverter

    converter = DocxToMarkdownConverter()
    outputPath = os.path.join(outputDir, outputFileName)
    imageDir = os.path.join(outputDir, "images")

    log(f"解析 DOCX: {inputPath}", "INFO", verbose)
    markdown = converter.convert_file(
        file_path=inputPath,
        output_path=outputPath,
        image_mode="file",
        image_dir=imageDir,
    )
    log(f"DOCX 解析完成，Markdown 长度: {len(markdown)}", "INFO", verbose)
    return markdown


# ============================================================
# 异步 worker 函数（各自自管异常，主线程从不 future.result）
# ============================================================

def _analyzeAndCache(
    pageNum: int,
    imageId: str,
    imgSource: dict,
    analyzer,
    cache,
    verbose: bool,
) -> None:
    """分析一张图 + 回写 cache。图像分析结果在流水线收尾阶段统一重建 .md。"""
    try:
        result = analyzer.analyzeImage(imgSource)
        cache.updateImageResult(pageNum, imageId, result)
        if result:
            log(f"  图片 {imageId} 分析成功: type={result.get('type')}", "DEBUG", verbose)
        else:
            log(f"  图片 {imageId} 分析失败", "WARN", verbose)
    except Exception as e:
        log(f"  图片 {imageId} worker 异常: {e}", "ERROR", True)
        try:
            cache.updateImageResult(pageNum, imageId, None)
        except Exception:
            pass


# ============================================================
# 主流程
# ============================================================

def runPipeline(args):
    """主管道流程（增量写入 + 断点续传版本）。"""
    verbose = args.verbose
    totalStart = time.time()

    logSeparator("files2MD 数据处理管道 启动", verbose)

    # 验证输入文件存在
    inputPath = os.path.abspath(args.input)
    if not os.path.isfile(inputPath):
        log(f"输入文件不存在: {inputPath}", "ERROR", True)
        sys.exit(1)

    # 计算 sanitized stem
    rawStem = Path(inputPath).stem
    sanitizedStem = re.sub(r'[\\/:*?"<>|]', '', rawStem).replace(' ', '_')
    outputFileName = f"{sanitizedStem}.md"

    # 创建输出目录
    if args.output_dir:
        outputDir = os.path.abspath(args.output_dir)
    else:
        outputDir = os.path.join(os.path.dirname(inputPath), sanitizedStem)
        log(f"未指定输出目录，自动创建: {outputDir}", "INFO", verbose)
    os.makedirs(outputDir, exist_ok=True)

    try:
        tasks = parse_tasks(args)
        validate_tasks(tasks, args.clean_cache)
    except ValueError as e:
        log(str(e), "ERROR", True)
        sys.exit(1)

    if getattr(args, "tasks", "") and (
        args.skip_image_analysis or args.reanalyze_images or args.enable_translation
    ):
        log("已提供 --tasks，忽略 --skip-image-analysis/--reanalyze-images/--enable-translation 兼容参数", "WARN", True)
    args.pipeline_tasks = tasks
    log(f"任务模式: {','.join(tasks)}", "INFO", verbose)

    # 从 .env 读取所有 API 配置
    imageApiUrl = os.environ.get("IMAGE_API_URL", "")
    imageApiKey = os.environ.get("IMAGE_API_KEY", "")
    imageApiModel = os.environ.get("IMAGE_API_MODEL", "qwen3.5")
    pdfApiUrl = os.environ.get("PDF_API_URL", "")
    pdfApiModel = os.environ.get("PDF_API_MODEL", "mineru2.5")
    translateApiUrl = os.environ.get("TRANSLATE_API_URL", "")
    translateApiKey = os.environ.get("TRANSLATE_API_KEY", "")
    translateApiModel = os.environ.get("TRANSLATE_API_MODEL", "claude-sonnet-4-20250514")

    # 图像分析 API Key 检查
    apiKey = ""
    if "image" in args.pipeline_tasks:
        apiKey = imageApiKey
        if not apiKey:
            log("未配置 IMAGE_API_KEY，跳过图像分析", "WARN", verbose)

    # 翻译 API 配置检查
    enableTranslation = "translate" in args.pipeline_tasks
    if enableTranslation:
        if not translateApiKey:
            translateApiKey = apiKey
        if not translateApiKey:
            log("未配置 TRANSLATE_API_KEY 且无 IMAGE_API_KEY，禁用翻译功能", "WARN", verbose)
            enableTranslation = False
        if not translateApiUrl:
            log("未配置 TRANSLATE_API_URL，禁用翻译功能", "WARN", verbose)
            enableTranslation = False

    # 将 env 配置同步到 args（供下游函数使用）
    args.image_api_url = imageApiUrl
    args.image_api_key = imageApiKey
    args.image_api_model = imageApiModel
    args.pdf_api_url = pdfApiUrl
    args.pdf_api_model = pdfApiModel
    args.translate_api_url = translateApiUrl
    args.translate_api_key = translateApiKey
    args.translate_api_model = translateApiModel

    # 初始化缓存
    cacheDir = os.path.join(outputDir, ".cache")
    cachePath = os.path.join(cacheDir, "progress.json")
    cache = CacheManager(cachePath, inputPath, args.image_mode, verbose)

    if args.clean_cache:
        cache.clean()
        log("已清理缓存", "INFO", verbose)

    cache.loadOrCreate()

    # 输出路径
    outputPath = os.path.join(outputDir, outputFileName)

    # ── 路径分支 ──────────────────────────────────────────────

    fileType = detectFileType(inputPath)
    log(f"文件类型: {fileType}", "INFO", verbose)

    if fileType == "docx":
        _runDocx(inputPath, outputDir, outputPath, outputFileName, cache, apiKey, args, verbose)

    else:
        _runPdfOrImage(
            inputPath, outputDir, outputPath, cache, apiKey,
            enableTranslation, translateApiUrl, translateApiKey, args, verbose,
        )

    totalElapsed = time.time() - totalStart
    logSeparator("管道完成", verbose)
    log(f"输出文件: {outputPath}", "DONE", verbose)
    log(f"总耗时: {totalElapsed:.2f}s", "INFO", verbose)
    logSeparator("", verbose)


# ============================================================
# Pipeline 上下文 + 新四阶段流程
# ============================================================

@dataclass
class PdfPipelineContext:
    inputPath: str
    outputDir: str
    outputPath: str
    cnOutputPath: Optional[str]
    cache: CacheManager
    tasks: tuple[str, ...]
    args: object = None
    verbose: bool = False
    imageDir: Optional[Path] = None


def _setupPdfPipeline(inputPath, outputDir, outputPath, cache, tasks, args, verbose) -> PdfPipelineContext:
    imageDir = Path(outputDir) / "images"
    imageDir.mkdir(parents=True, exist_ok=True)
    cnOutputPath = os.path.join(outputDir, f"{Path(outputPath).stem}_cn.md") if "translate" in tasks else None
    cache.resetRunningStates()
    return PdfPipelineContext(
        inputPath=inputPath,
        outputDir=outputDir,
        outputPath=outputPath,
        cnOutputPath=cnOutputPath,
        cache=cache,
        tasks=tasks,
        args=args,
        verbose=verbose,
        imageDir=imageDir,
    )


def _requireParsedCache(ctx: PdfPipelineContext, taskName: str) -> None:
    if not ctx.cache.hasParsedPages():
        log(f"--tasks {taskName} 需要已有解析缓存，请先运行 --tasks parse", "ERROR", True)
        sys.exit(1)


def _writeTextFile(path: str, content: str) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())


def _runImageTask(ctx: PdfPipelineContext, apiKey: str) -> None:
    _requireParsedCache(ctx, "image")
    if not apiKey:
        log("未配置 IMAGE_API_KEY，无法执行 image 任务", "WARN", True)
        _writeTextFile(ctx.outputPath, ctx.cache.rebuildMarkdown())
        return

    analyzer = ImageAnalyzer(
        apiUrl=ctx.args.image_api_url,
        apiKey=apiKey,
        model=ctx.args.image_api_model,
        maxConcurrent=ctx.args.max_concurrent,
        maxRetry=ctx.args.retry,
        retryDelay=ctx.args.retry_delay,
        verbose=ctx.verbose,
    )
    pending = list(ctx.cache.iterPendingImages())
    log(f"待分析图片: {len(pending)}", "INFO", ctx.verbose)

    with ThreadPoolExecutor(max_workers=analyzer.maxConcurrent) as executor:
        futures = []
        for page, img in pending:
            ctx.cache.markImageRunning(page["pageNum"], img["imageId"])
            futures.append(executor.submit(
                _analyzeAndCache,
                page["pageNum"],
                img["imageId"],
                {"path": img["absPath"], "format": img.get("format", "png")},
                analyzer,
                ctx.cache,
                ctx.verbose,
            ))
        for future in futures:
            future.result()

    _finalizeImageOutput(ctx)


def _submitImageFutures(ctx: PdfPipelineContext, analyzer, executor, pagesWithImages) -> list:
    futures = []
    for page, images in pagesWithImages:
        for img in images:
            ctx.cache.markImageRunning(page["pageNum"], img["imageId"])
            futures.append(executor.submit(
                _analyzeAndCache,
                page["pageNum"],
                img["imageId"],
                {"path": img["absPath"], "format": img.get("format", "png")},
                analyzer,
                ctx.cache,
                ctx.verbose,
            ))
    return futures


def _submitTranslationFuture(ctx: PdfPipelineContext, translator, executor, page) -> object:
    ctx.cache.markTranslationRunning(page["pageNum"])
    return executor.submit(
        _translateAndCache,
        page["pageNum"],
        page.get("rawMarkdown", ""),
        translator,
        ctx.cache,
        ctx.verbose,
    )


def _finalizeImageOutput(ctx: PdfPipelineContext) -> None:
    for page in ctx.cache._data["pages"]:
        if page.get("parseStatus") != "completed":
            continue
        finalMd = buildFinalMarkdownFromCache(page, ctx.outputDir, ctx.verbose)
        ctx.cache.finalizePage(page["pageNum"], finalMd)
    _writeTextFile(ctx.outputPath, ctx.cache.rebuildMarkdown())
    log(f"图片理解输出已重建: {ctx.outputPath}", "DONE", ctx.verbose)


def _finalizeTranslationOutput(ctx: PdfPipelineContext) -> None:
    if ctx.cnOutputPath:
        _writeTextFile(ctx.cnOutputPath, ctx.cache.rebuildTranslationMarkdown())
        log(f"翻译输出已重建: {ctx.cnOutputPath}", "DONE", ctx.verbose)


def _createImageAnalyzer(ctx: PdfPipelineContext, apiKey: str):
    if not apiKey:
        return None
    return ImageAnalyzer(
        apiUrl=ctx.args.image_api_url,
        apiKey=apiKey,
        model=ctx.args.image_api_model,
        maxConcurrent=ctx.args.max_concurrent,
        maxRetry=ctx.args.retry,
        retryDelay=ctx.args.retry_delay,
        verbose=ctx.verbose,
    )


def _createTranslator(ctx: PdfPipelineContext, translateApiUrl: str, translateApiKey: str):
    if not translateApiKey or not translateApiUrl:
        return None
    from core.translator import Translator

    return Translator(
        apiUrl=translateApiUrl,
        apiKey=translateApiKey,
        model=ctx.args.translate_api_model,
        maxConcurrent=ctx.args.max_concurrent_translate,
        maxRetry=ctx.args.translate_retry,
        retryDelay=ctx.args.translate_retry_delay,
        verbose=ctx.verbose,
    )


def _translateAndCache(pageNum: int, rawText: str, translator, cache, verbose: bool) -> None:
    try:
        result = translator.translate(rawText)
        cache.updateTranslationResult(pageNum, result)
        if result:
            log(f"第 {pageNum} 页翻译完成", "INFO", verbose)
        else:
            log(f"第 {pageNum} 页翻译失败", "WARN", True)
    except Exception as e:
        log(f"第 {pageNum} 页翻译 worker 异常: {e}", "ERROR", True)
        cache.updateTranslationResult(pageNum, None)


def _runTranslateTask(ctx: PdfPipelineContext, translateApiUrl: str, translateApiKey: str) -> None:
    _requireParsedCache(ctx, "translate")
    if not translateApiKey or not translateApiUrl:
        log("未配置 TRANSLATE_API_URL 或 TRANSLATE_API_KEY，无法执行 translate 任务", "WARN", True)
        return

    from core.translator import Translator

    translator = Translator(
        apiUrl=translateApiUrl,
        apiKey=translateApiKey,
        model=ctx.args.translate_api_model,
        maxConcurrent=ctx.args.max_concurrent_translate,
        maxRetry=ctx.args.translate_retry,
        retryDelay=ctx.args.translate_retry_delay,
        verbose=ctx.verbose,
    )
    pending = list(ctx.cache.iterPendingTranslations())
    log(f"待翻译页面: {len(pending)}", "INFO", ctx.verbose)

    with ThreadPoolExecutor(max_workers=translator.maxConcurrent) as executor:
        futures = []
        for page in pending:
            ctx.cache.markTranslationRunning(page["pageNum"])
            futures.append(executor.submit(
                _translateAndCache,
                page["pageNum"],
                page.get("rawMarkdown", ""),
                translator,
                ctx.cache,
                ctx.verbose,
            ))
        for future in futures:
            future.result()

    _finalizeTranslationOutput(ctx)


def _runParsePipelineTask(ctx: PdfPipelineContext, apiKey: str, translateApiUrl: str, translateApiKey: str) -> None:
    from core.parseFlowApi import createMinerUClient, loadInputImages, parseSinglePage

    logSeparator("PDF/Image 解析（任务模式）", ctx.verbose)
    client = createMinerUClient(
        apiUrl=ctx.args.pdf_api_url.rstrip("/"),
        modelName=ctx.args.pdf_api_model,
        serverTimeout=ctx.args.server_timeout,
        verbose=ctx.verbose,
    )
    pageImages = loadInputImages(ctx.inputPath, ctx.args.dpi, ctx.verbose)
    breakpoint = ctx.cache.findBreakpoint()
    log(f"共 {len(pageImages)} 页，从第 {breakpoint} 页开始解析", "INFO", ctx.verbose)

    analyzer = _createImageAnalyzer(ctx, apiKey) if "image" in ctx.tasks else None
    translator = _createTranslator(ctx, translateApiUrl, translateApiKey) if "translate" in ctx.tasks else None

    if "image" in ctx.tasks and analyzer is None:
        log("未配置 IMAGE_API_KEY，无法执行 image 任务", "WARN", True)
    if "translate" in ctx.tasks and translator is None:
        log("未配置 TRANSLATE_API_URL 或 TRANSLATE_API_KEY，无法执行 translate 任务", "WARN", True)

    imageExecutor = ThreadPoolExecutor(max_workers=analyzer.maxConcurrent) if analyzer else None
    translateExecutor = ThreadPoolExecutor(max_workers=translator.maxConcurrent) if translator else None
    imageFutures = []
    translateFutures = []

    try:
        for pageNum, img in pageImages:
            if pageNum < breakpoint:
                continue
            rawMd, absPaths = parseSinglePage(client, pageNum, img, ctx.imageDir, ctx.verbose)
            images = []
            for i, absPath in enumerate(absPaths):
                fmt = "png"
                if absPath.lower().endswith((".jpg", ".jpeg")):
                    fmt = "jpeg"
                elif absPath.lower().endswith(".webp"):
                    fmt = "webp"
                images.append({
                    "imageId": f"p{pageNum:03d}_img{i:02d}",
                    "relPath": f"images/{Path(absPath).name}",
                    "absPath": absPath,
                    "analysisStatus": "pending",
                    "format": fmt,
                })
            ctx.cache.addPage(pageNum, rawMd, images)
            page = ctx.cache.getPage(pageNum)
            log(f"第 {pageNum} 页解析结果已写入缓存", "INFO", ctx.verbose)

            if analyzer and imageExecutor and images and page:
                imageFutures.extend(_submitImageFutures(ctx, analyzer, imageExecutor, [(page, images)]))
                log(f"第 {pageNum} 页已提交 {len(images)} 个图片理解任务", "DEBUG", ctx.verbose)
            if translator and translateExecutor and page:
                translateFutures.append(_submitTranslationFuture(ctx, translator, translateExecutor, page))
                log(f"第 {pageNum} 页已提交翻译任务", "DEBUG", ctx.verbose)

        for future in imageFutures:
            future.result()
        for future in translateFutures:
            future.result()
    finally:
        if imageExecutor:
            imageExecutor.shutdown(wait=True)
        if translateExecutor:
            translateExecutor.shutdown(wait=True)

    if analyzer:
        _finalizeImageOutput(ctx)
    else:
        _writeTextFile(ctx.outputPath, ctx.cache.rebuildMarkdown())
        log(f"解析输出已重建: {ctx.outputPath}", "DONE", ctx.verbose)
    if translator:
        _finalizeTranslationOutput(ctx)


def _runCacheOnlyPostprocessors(ctx: PdfPipelineContext, apiKey: str, translateApiUrl: str, translateApiKey: str) -> None:
    _requireParsedCache(ctx, ",".join(ctx.tasks))

    imageError = None
    translateError = None

    def run_image():
        nonlocal imageError
        try:
            _runImageTask(ctx, apiKey)
        except Exception as e:
            imageError = e

    def run_translate():
        nonlocal translateError
        try:
            _runTranslateTask(ctx, translateApiUrl, translateApiKey)
        except Exception as e:
            translateError = e

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = []
        if "image" in ctx.tasks:
            futures.append(executor.submit(run_image))
        if "translate" in ctx.tasks:
            futures.append(executor.submit(run_translate))
        for future in futures:
            future.result()

    if imageError:
        raise imageError
    if translateError:
        raise translateError


def _runPdfOrImage(
    inputPath, outputDir, outputPath, cache, apiKey,
    enableTranslation, translateApiUrl, translateApiKey, args, verbose,
):
    tasks = args.pipeline_tasks
    ctx = _setupPdfPipeline(inputPath, outputDir, outputPath, cache, tasks, args, verbose)
    try:
        if "parse" in tasks:
            _runParsePipelineTask(ctx, apiKey, translateApiUrl, translateApiKey)
        elif len(tasks) > 1:
            _runCacheOnlyPostprocessors(ctx, apiKey, translateApiUrl, translateApiKey)
        else:
            if "image" in tasks:
                _runImageTask(ctx, apiKey)
            if "translate" in tasks:
                _runTranslateTask(ctx, translateApiUrl, translateApiKey)
    except KeyboardInterrupt:
        log("收到中断信号，已保留完成单元的缓存；下次运行将继续未完成任务", "WARN", True)
        raise

    _applyImageModeToFile(outputPath, outputDir, args.image_mode, verbose)


def _runDocx(inputPath, outputDir, outputPath, outputFileName, cache, apiKey, args, verbose):
    """DOCX 简化缓存流程。"""
    docxData = cache._data.get("docx")

    if docxData and docxData.get("status") == "completed":
        log("DOCX 已完成（缓存），跳过", "INFO", verbose)
        return

    # 解析 DOCX
    if docxData is None or docxData.get("status") not in ("parsed", "analyzing"):
        markdown = parseDocx(inputPath, outputDir, outputFileName, verbose)

        # 提取图片信息
        imageSources = extractImageSources(markdown, outputDir, verbose)
        images = []
        for i, (_, src) in enumerate(imageSources):
            absPath = src.get("path", "")
            images.append({
                "imageId": f"docx_img{i:02d}",
                "relPath": os.path.relpath(absPath, outputDir) if absPath else "",
                "absPath": absPath,
                "status": "pending",
                "format": src.get("format", "png"),
            })

        cache.setDocxRaw(markdown, images)

        # 先写出原始 markdown
        with open(outputPath, "w", encoding="utf-8") as f:
            f.write(markdown)
        log("DOCX 原始 Markdown 已写出", "INFO", verbose)
    else:
        # 从缓存恢复
        markdown = docxData["rawMarkdown"]
        images = docxData.get("images", [])
        log(f"从缓存恢复 DOCX，跳过解析，{len(images)} 张图片待处理", "INFO", verbose)

    # 图像分析
    if apiKey and images:
        analyzer = ImageAnalyzer(
            apiUrl=args.image_api_url,
            apiKey=apiKey,
            model=args.image_api_model,
            maxConcurrent=args.max_concurrent,
            maxRetry=args.retry,
            retryDelay=args.retry_delay,
            verbose=verbose,
        )

        # 只分析 pending 状态的图片，使用并发批量分析
        pendingImages = [img for img in images if img["status"] == "pending"]
        log(f"待分析图片: {len(pendingImages)}/{len(images)}", "INFO", verbose)

        if pendingImages:
            sources = [{"path": img["absPath"], "format": img.get("format", "png")}
                       for img in pendingImages]
            results = analyzer.analyzeImages(sources)
            for imgInfo, result in zip(pendingImages, results):
                cache.updateDocxImageResult(imgInfo["imageId"], result)
                if result:
                    log(f"图片 {imgInfo['imageId']} 分析成功", "DEBUG", verbose)

        # 生成 finalMarkdown
        finalMd = buildFinalMarkdownFromCache(cache._data["docx"], outputDir, verbose)
        cache.setDocxFinal(finalMd)

        # 覆写 .md
        with open(outputPath, "w", encoding="utf-8") as f:
            f.write(finalMd)
        log("DOCX 最终 Markdown 已写出", "INFO", verbose)

    # image-mode 后处理
    _applyImageModeToFile(outputPath, outputDir, args.image_mode, verbose)


def _applyImageModeToFile(outputPath, outputDir, imageMode, verbose):
    """对已写出的 .md 文件执行 image-mode 后处理。"""
    if imageMode == "none":
        log("image-mode=none: 移除图片引用并清理图片文件", "STEP", verbose)
        with open(outputPath, "r", encoding="utf-8") as f:
            markdown = f.read()
        markdown = applyImageMode(markdown, outputDir, imageMode, verbose)
        with open(outputPath, "w", encoding="utf-8") as f:
            f.write(markdown)


# ============================================================
# CLI
# ============================================================

def buildArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="files2MD 数据处理管道 — 统一入口，支持 PDF/DOCX/Image → Markdown + 图像理解",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
使用示例：
  # 所有 API 配置请写在 .env 文件中（IMAGE_API_URL, TRANSLATE_API_URL 等）

  # 解析 DOCX（自动在输入文件同级目录创建输出）
  python pipeline.py -i input.docx --verbose

  # 仅解析 PDF
  python pipeline.py -i input.pdf --tasks parse --verbose

  # 解析 PDF + 图片理解
  python pipeline.py -i input.pdf --tasks parse,image --verbose

  # 解析 PDF + 翻译
  python pipeline.py -i input.pdf --tasks parse,translate --verbose

  # 解析 PDF + 图片理解 + 翻译
  python pipeline.py -i input.pdf --tasks parse,image,translate --verbose

  # 在解析完成后，仅补做图片理解
  python pipeline.py -i input.pdf --tasks image --verbose

  # 在解析完成后，仅补做翻译
  python pipeline.py -i input.pdf --tasks translate --verbose

  # 在解析完成后，补做图片理解和翻译
  python pipeline.py -i input.pdf --tasks image,translate --verbose

  # 使用 image-mode=none（仅保留分析结果，不保留原图）
  python pipeline.py -i input.pdf --image-mode none --verbose

  # 重新对已有输出执行图像理解（API 之前失败后重试）
  python pipeline.py -i report.pdf --reanalyze-images --verbose

  # 强制从头开始（清理缓存）
  python pipeline.py -i input.pdf --clean-cache --verbose
        """
    )

    # 必填参数
    parser.add_argument("-i", "--input", type=str, required=True,
                        help="输入文件路径 (pdf/docx/png/jpg/jpeg/bmp/tiff/tif/webp)")
    parser.add_argument("-o", "--output-dir", type=str, default="",
                        help="输出目录路径（默认: 输入文件同级目录下以文件名命名的子目录）")

    # 图片处理
    parser.add_argument("--image-mode", type=str, default="file",
                        choices=["file", "base64", "none"],
                        help="图片处理模式（默认: file）")
    parser.add_argument("--tasks", type=str, default="",
                        help=("显式任务列表，逗号分隔：parse,image,translate。\n"
                              "示例: --tasks parse 或 --tasks image,translate。\n"
                              "默认等价于 parse,image；提供后会覆盖旧的 skip/reanalyze/enable 参数。"))
    parser.add_argument("--skip-image-analysis", action="store_true",
                        help="跳过图像理解分析")
    parser.add_argument("--reanalyze-images", action="store_true",
                        help="重新对已有输出 MD 执行图像理解（跳过文件解析步骤）")

    # PDF 渲染参数
    parser.add_argument("--dpi", type=int, default=300,
                        help="PDF 渲染 DPI（默认: 300）")
    parser.add_argument("--server-timeout", type=int, default=600,
                        help="MinerU API 超时秒数（默认: 600）")

    # 并发与重试
    parser.add_argument("--max-concurrent", type=int, default=3,
                        help="最大并发图像理解请求数（默认: 3）")
    parser.add_argument("--retry", type=int, default=10,
                        help="图像理解 API 失败重试次数（默认: 10）")
    parser.add_argument("--retry-delay", type=float, default=15.0,
                        help="重试间隔秒数（默认: 15）")

    # 翻译功能
    parser.add_argument("--enable-translation", action="store_true",
                        help="开启翻译功能，生成 _cn.md 中文翻译文件")
    parser.add_argument("--max-concurrent-translate", type=int, default=5,
                        help="最大并发翻译请求数（默认: 2）")
    parser.add_argument("--translate-retry", type=int, default=10,
                        help="翻译失败重试次数（默认: 10）")
    parser.add_argument("--translate-retry-delay", type=float, default=30.0,
                        help="翻译重试间隔秒数（默认: 30）")

    # 日志
    parser.add_argument("--clean-cache", action="store_true",
                        help="清理缓存，强制从头开始")
    parser.add_argument("--verbose", action="store_true",
                        help="打印详细日志")

    return parser


def main():
    parser = buildArgParser()
    args = parser.parse_args()
    runPipeline(args)


if __name__ == "__main__":
    main()
