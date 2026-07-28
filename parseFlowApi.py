'''
Author: wilbur
Version: 2.7
  Date: 2026-05-21
  Description: verbose 打印 MinerU 原始 blocks

Version: 2.6
  Date: 2026-04-23
  Description: 新增 createMinerUClient() 和 parseSinglePage() 逐页接口

Version: 2.5
  Date: 2026-04-21
  Description: convertPdfOrImage() 移除内部文件写入，写入由调用方（pipeline）负责

Version: 2.4
  Date: 2026-04-14
  Description: convertPdfOrImage() 新增 imageDirName 和 onImageExtracted 参数

Version: 2.3
  Date: 2026-04-13
  Description: 新增 convertPdfOrImage() 函数供 pipeline.py 调用

Version: 2.2
  Date: 2026-04-13
  Description: 新增 README 文档，移除 docx2MD.py 和 docs 目录

Version: 2.1
  Date: 2026-04-10
  Description: 新增 image block 裁图保存，Markdown 中插入图片引用

Version: 2.0
  Date: 2026-04-10
  Description: 使用官方 mineru_vl_utils 库重写
               backend=http-client，直接调用 MinerUClient.two_step_extract
               Stage I/II prompt、采样参数、图片预处理全部走官方实现

Version: 1.1
  Date: 2026-04-10
  Description: 修复 stageTwoContentRecognition 中 lambda 闭包 NameError

Version: 1.0
  Date: 2026-04-10
  Description: MinerU2.5 API 调用版解析流水线（自定义两阶段，已废弃）
'''

import sys
import json
import time
import argparse
from datetime import datetime
from pathlib import Path

from PIL import Image
from mineru_vl_utils import MinerUClient
from mineru_vl_utils.structs import BlockType


# ============================================================
# 日志工具
# ============================================================

def log(msg: str, level: str = "INFO", verbose: bool = True):
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
    print(f"{timestamp} {prefix} {msg}", flush=True)


def logSeparator(title: str = "", verbose: bool = True):
    if not verbose:
        return
    pad = (58 - len(title)) // 2
    if title:
        print(f"\033[35m{'=' * pad} {title} {'=' * (58 - pad - len(title))}\033[0m", flush=True)
    else:
        print(f"\033[35m{'=' * 60}\033[0m", flush=True)


def logRawParseOutput(pageNum: int, blocks, verbose: bool) -> None:
    if not verbose:
        return
    try:
        raw = json.dumps(blocks, ensure_ascii=False, default=str, indent=2)
    except Exception:
        raw = str(blocks)
    log(f"第 {pageNum} 页解析原始输出:\n{raw}", "DEBUG", True)


# ============================================================
# PDF / 图片 → PIL Image 列表
# ============================================================

def loadInputImages(inputPath: str, dpi: int, verbose: bool) -> list:
    try:
        import pypdfium2 as pdfium
    except ImportError:
        log("缺少 pypdfium2，请安装: pip install pypdfium2", "ERROR", verbose)
        sys.exit(1)

    path = Path(inputPath)
    if not path.exists():
        log(f"输入文件不存在: {inputPath}", "ERROR", verbose)
        sys.exit(1)

    suffix = path.suffix.lower()
    log(f"输入文件: {path.name}  格式: {suffix}  DPI: {dpi}", "INFO", verbose)

    if suffix == ".pdf":
        log("渲染 PDF 各页为图片...", "STEP", verbose)
        pdf = pdfium.PdfDocument(str(path))
        totalPages = len(pdf)
        log(f"PDF 共 {totalPages} 页", "INFO", verbose)
        scale = dpi / 72.0
        pages = []
        for i, page in enumerate(pdf):
            log(f"  渲染第 {i+1}/{totalPages} 页...", "DEBUG", verbose)
            bitmap = page.render(scale=scale, rotation=0)
            img = bitmap.to_pil().convert("RGB")
            pages.append((i + 1, img))
            log(f"  第 {i+1} 页 → {img.size[0]}×{img.size[1]} px", "DEBUG", verbose)
            page.close()
        pdf.close()
        log(f"PDF 渲染完成，共 {len(pages)} 页", "INFO", verbose)
        return pages

    elif suffix in (".png", ".jpg", ".jpeg", ".bmp", ".tiff", ".tif", ".webp"):
        log("加载图片...", "STEP", verbose)
        img = Image.open(str(path)).convert("RGB")
        log(f"图片尺寸: {img.size[0]}×{img.size[1]} px", "INFO", verbose)
        return [(1, img)]

    else:
        log(f"不支持的格式: {suffix}", "ERROR", verbose)
        sys.exit(1)


# ============================================================
# ContentBlock 列表 → Markdown
# ============================================================

_SKIP_TYPES = {
    BlockType.IMAGE,
    BlockType.PHONETIC,
    BlockType.EQUATION_BLOCK,  # post_process 已合并到 equation
}

_META_TYPES = {
    BlockType.HEADER,
    BlockType.FOOTER,
    BlockType.PAGE_NUMBER,
    BlockType.ASIDE_TEXT,
}


def saveImageBlocks(
    blocks: list,
    pageImg: Image.Image,
    pageNum: int,
    imageDir: Path,
    verbose: bool,
) -> dict:
    """裁剪并保存 image 类型 block，返回 {block_index: 相对路径} 映射"""
    imgPaths = {}
    imgCount = 0
    w, h = pageImg.size
    for idx, block in enumerate(blocks):
        if block.get("type") != BlockType.IMAGE:
            continue
        bbox = block.get("bbox", [])
        if len(bbox) != 4:
            continue
        x1, y1, x2, y2 = int(bbox[0]*w), int(bbox[1]*h), int(bbox[2]*w), int(bbox[3]*h)
        cropped = pageImg.crop((x1, y1, x2, y2))
        imgCount += 1
        fname = f"page{pageNum:03d}_img{imgCount:02d}.png"
        savePath = imageDir / fname
        cropped.save(str(savePath))
        imgPaths[idx] = fname
        log(f"    → image block[{idx}] 已保存: {fname}  ({cropped.size[0]}×{cropped.size[1]})", "DEBUG", verbose)
    return imgPaths


def blocksToMarkdown(
    blocks: list,
    pageNum: int,
    keepMeta: bool,
    verbose: bool,
    imgPaths: dict | None = None,
    imageDir: str = "images",
) -> str:
    lines = []
    if keepMeta:
        lines.append(f"\n<!-- Page {pageNum} -->\n")

    for idx, block in enumerate(blocks):
        btype = block.get("type", BlockType.TEXT)
        content = (block.get("content") or "").strip()

        if btype in _META_TYPES and not keepMeta:
            continue

        # image block：插入图片引用（无论 content 是否为空）
        if btype == BlockType.IMAGE:
            if imgPaths and idx in imgPaths:
                relPath = f"{imageDir}/{imgPaths[idx]}"
                log(f"    → image                [图片] {imgPaths[idx]}", "DEBUG", verbose)
                lines.append(f"\n![图片 page{pageNum}-{idx}]({relPath})\n")
            continue

        if btype in _SKIP_TYPES:
            continue
        if not content:
            continue

        log(f"    → {btype:<20} {content[:60]}{'...' if len(content) > 60 else ''}", "DEBUG", verbose)

        if btype == BlockType.TITLE:
            lines.append(f"\n# {content}\n")
        elif btype == BlockType.CODE or btype == BlockType.ALGORITHM:
            lines.append(f"\n```\n{content}\n```\n")
        elif btype == BlockType.TABLE:
            lines.append(f"\n{content}\n")
        elif btype == BlockType.EQUATION:
            # MinerU 库 post_process 已添加 \[...\]，与 $$ 重复，需先清除
            cleaned = content.strip()
            if cleaned.startswith("\\["):
                cleaned = cleaned[2:]
            if cleaned.endswith("\\]"):
                cleaned = cleaned[:-2]
            cleaned = cleaned.strip()
            lines.append(f"\n$$\n{cleaned}\n$$\n")
        elif btype in (BlockType.TABLE_CAPTION, BlockType.IMAGE_CAPTION,
                       BlockType.CODE_CAPTION, BlockType.TABLE_FOOTNOTE,
                       BlockType.IMAGE_FOOTNOTE):
            lines.append(f"\n_{content}_\n")
        elif btype in _META_TYPES and keepMeta:
            lines.append(f"\n<!-- {btype}: {content} -->\n")
        else:
            lines.append(f"\n{content}\n")

    return "\n".join(lines)


# ============================================================
# 可复用转换函数（供 pipeline.py 等外部模块调用）
# ============================================================

def convertPdfOrImage(
    inputPath: str,
    outputDir: str,
    apiUrl: str = "http://192.168.110.208:9091",
    modelName: str = "mineru2.5",
    dpi: int = 300,
    serverTimeout: int = 600,
    verbose: bool = False,
    imageDirName: str = "images",
    onImageExtracted: "callable | None" = None,
) -> tuple[str, list[str]]:
    """将 PDF 或 Image 转换为 Markdown。

    参数:
        inputPath: 输入文件路径（PDF 或图片）
        outputDir: 输出目录路径
        apiUrl: MinerU API 地址
        modelName: MinerU 模型名
        dpi: PDF 渲染 DPI
        serverTimeout: API 超时秒数
        verbose: 是否输出详细日志

    返回:
        (markdown_text, [提取的图片绝对路径列表])

    注意:
        Markdown 文件会保存到 outputDir/{input_stem}.md
    """
    totalStart = time.time()

    # Step 1: 初始化客户端
    logSeparator("Step 1  初始化 MinerUClient", verbose)
    client = MinerUClient(
        backend="http-client",
        server_url=apiUrl,
        model_name=modelName,
        http_timeout=serverTimeout,
        max_concurrency=1,
        max_connections=1,
        use_tqdm=verbose,
    )
    log("MinerUClient 初始化完成", "INFO", verbose)

    # Step 2: 加载输入
    logSeparator("Step 2  输入加载 & 渲染", verbose)
    pageImages = loadInputImages(inputPath, dpi, verbose)

    # Step 3: 逐页解析
    logSeparator("Step 3  逐页解析", verbose)
    outDir = Path(outputDir)
    outDir.mkdir(parents=True, exist_ok=True)
    imageDir = outDir / imageDirName
    imageDir.mkdir(parents=True, exist_ok=True)
    log(f"图片输出目录: {imageDir}", "INFO", verbose)

    allResults = []
    allImagePaths = []  # 汇总所有图片绝对路径

    for idx, (pageNum, img) in enumerate(pageImages):
        log(f"处理第 {idx+1}/{len(pageImages)} 页...", "STEP", verbose)
        t0 = time.time()
        blocks = client.two_step_extract(img)
        elapsed = time.time() - t0
        log(f"  第 {pageNum} 页完成，{len(blocks)} 个 block，耗时 {elapsed:.2f}s", "INFO", verbose)

        imgPaths = saveImageBlocks(blocks, img, pageNum, imageDir, verbose)
        # 汇总图片绝对路径
        for fname in imgPaths.values():
            absPath = str(imageDir / fname)
            allImagePaths.append(absPath)
            if onImageExtracted:
                onImageExtracted(absPath)

        md = blocksToMarkdown(blocks, pageNum, keepMeta=False, verbose=verbose, imgPaths=imgPaths, imageDir=imageDirName)
        allResults.append({"page": pageNum, "blocks": blocks, "markdown": md, "elapsed": elapsed})

    # Step 4: 组装 Markdown
    logSeparator("Step 4  组装 Markdown", verbose)
    finalMd = "\n\n---\n\n".join(r["markdown"] for r in allResults).strip()
    log(f"Markdown 总字符数: {len(finalMd)}", "INFO", verbose)

    # 统计
    totalElapsed = time.time() - totalStart
    pageElapsed = [r["elapsed"] for r in allResults]
    logSeparator("完成统计", verbose)
    log(f"总页数:     {len(pageImages)}", "INFO", verbose)
    log(f"总耗时:     {totalElapsed:.2f}s", "INFO", verbose)
    log(f"输出目录:   {outDir.resolve()}", "DONE", verbose)

    return finalMd, allImagePaths


# ============================================================
# 主流程
# ============================================================

def runPipeline(
    inputPath: str,
    outputDir: str,
    apiBaseUrl: str,
    modelName: str,
    dpi: int,
    keepMeta: bool,
    saveImages: bool,
    serverTimeout: int,
    verbose: bool,
):
    logSeparator("MinerU2.5 解析流水线 启动", verbose)
    log(f"输入文件:  {inputPath}", "INFO", verbose)
    log(f"输出目录:  {outputDir}", "INFO", verbose)
    log(f"API 地址:  {apiBaseUrl}", "INFO", verbose)
    log(f"模型名称:  {modelName}", "INFO", verbose)
    totalStart = time.time()

    # ── Step 1: 初始化官方客户端 ─────────────────────────────
    logSeparator("Step 1  初始化 MinerUClient", verbose)
    log("使用 backend=http-client，连接远程 vLLM server...", "INFO", verbose)
    client = MinerUClient(
        backend="http-client",
        server_url=apiBaseUrl,
        model_name=modelName,
        http_timeout=serverTimeout,
        max_concurrency=1,
        max_connections=1,
        use_tqdm=verbose,
    )
    log("MinerUClient 初始化完成", "INFO", verbose)

    # ── Step 2: 加载输入 ─────────────────────────────────────
    logSeparator("Step 2  输入加载 & 渲染", verbose)
    pageImages = loadInputImages(inputPath, dpi, verbose)

    # ── Step 3: 创建输出目录 ─────────────────────────────────
    logSeparator("Step 3  创建输出目录", verbose)
    outDir = Path(outputDir)
    outDir.mkdir(parents=True, exist_ok=True)
    log(f"输出目录: {outDir.resolve()}", "INFO", verbose)

    if saveImages:
        imgDir = outDir / "page_images"
        imgDir.mkdir(parents=True, exist_ok=True)
        for pageNum, img in pageImages:
            savePath = imgDir / f"page_{pageNum:04d}.png"
            img.save(str(savePath))
            log(f"  已保存: {savePath}", "DEBUG", verbose)
        log(f"页面图片已保存至: {imgDir}", "INFO", verbose)

    # ── Step 4: 逐页解析 ─────────────────────────────────────
    logSeparator("Step 4  逐页解析（官方 two_step_extract）", verbose)
    log(f"共 {len(pageImages)} 页，每页调用 client.two_step_extract()", "INFO", verbose)

    imageDir = outDir / "images"
    imageDir.mkdir(parents=True, exist_ok=True)
    log(f"图片输出目录: {imageDir}", "INFO", verbose)

    allResults = []

    for idx, (pageNum, img) in enumerate(pageImages):
        log(f"处理第 {idx+1}/{len(pageImages)} 页...", "STEP", verbose)
        t0 = time.time()
        blocks = client.two_step_extract(img)
        elapsed = time.time() - t0
        log(f"  第 {pageNum} 页完成，{len(blocks)} 个 block，耗时 {elapsed:.2f}s", "INFO", verbose)

        imgPaths = saveImageBlocks(blocks, img, pageNum, imageDir, verbose)
        md = blocksToMarkdown(blocks, pageNum, keepMeta, verbose, imgPaths=imgPaths, imageDir="images")
        allResults.append({"page": pageNum, "blocks": blocks, "markdown": md, "elapsed": elapsed})

    # ── Step 5: 组装 Markdown ────────────────────────────────
    logSeparator("Step 5  组装 Markdown", verbose)
    finalMd = "\n\n---\n\n".join(r["markdown"] for r in allResults).strip()
    log(f"Markdown 总字符数: {len(finalMd)}", "INFO", verbose)

    # ── Step 6: 写出文件 ─────────────────────────────────────
    logSeparator("Step 6  写出文件", verbose)
    mdFileName = Path(inputPath).stem + ".md"
    outPath = outDir / mdFileName
    outPath.write_text(finalMd, encoding="utf-8")
    log(f"Markdown 写出成功: {outPath.resolve()}", "DONE", verbose)

    # ── 汇总统计 ─────────────────────────────────────────────
    totalElapsed = time.time() - totalStart
    pageElapsed = [r["elapsed"] for r in allResults]
    logSeparator("完成统计", verbose)
    log(f"总页数:     {len(pageImages)}", "INFO", verbose)
    log(f"总耗时:     {totalElapsed:.2f}s", "INFO", verbose)
    log(f"页均耗时:   {sum(pageElapsed)/max(len(pageElapsed),1):.2f}s/页", "INFO", verbose)
    maxIdx = pageElapsed.index(max(pageElapsed))
    log(f"最慢一页:   {max(pageElapsed):.2f}s（页 {allResults[maxIdx]['page']}）", "INFO", verbose)
    log(f"输出目录:   {outDir.resolve()}", "DONE", verbose)
    log(f"输出 MD:    {outPath.resolve()}", "DONE", verbose)
    logSeparator("流水线结束", verbose)


# ============================================================
# 逐页可调用接口（供 pipeline 增量模式使用）
# ============================================================

def createMinerUClient(
    apiUrl: str = "http://192.168.110.208:9091",
    modelName: str = "mineru2.5",
    serverTimeout: int = 600,
    verbose: bool = False,
) -> MinerUClient:
    """一次性初始化 MinerUClient，供后续逐页调用复用。"""
    client = MinerUClient(
        backend="http-client",
        server_url=apiUrl,
        model_name=modelName,
        http_timeout=serverTimeout,
        max_concurrency=1,
        max_connections=1,
        use_tqdm=verbose,
    )
    log("MinerUClient 初始化完成", "INFO", verbose)
    return client


def parseSinglePage(
    client: MinerUClient,
    pageNum: int,
    img: Image.Image,
    imageDir: Path,
    verbose: bool = False,
    onImageExtracted: "callable | None" = None,
) -> tuple[str, list[str]]:
    """解析单页，返回 (markdown, [图片绝对路径列表])。

    参数:
        client: 已初始化的 MinerUClient 实例
        pageNum: 页码（从 1 开始）
        img: PIL Image 对象
        imageDir: 图片保存目录（Path 对象）
        verbose: 是否输出详细日志
        onImageExtracted: 图片提取完成后的回调，参数为绝对路径

    返回:
        (该页 markdown, [提取的图片绝对路径列表])
    """
    imageDir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    blocks = client.two_step_extract(img)
    logRawParseOutput(pageNum, blocks, verbose)
    elapsed = time.time() - t0
    log(f"第 {pageNum} 页解析完成，{len(blocks)} 个 block，耗时 {elapsed:.2f}s", "INFO", verbose)

    imgPaths = saveImageBlocks(blocks, img, pageNum, imageDir, verbose)

    absPaths = []
    for fname in imgPaths.values():
        absPath = str(imageDir / fname)
        absPaths.append(absPath)
        if onImageExtracted:
            onImageExtracted(absPath)

    md = blocksToMarkdown(
        blocks, pageNum, keepMeta=False, verbose=verbose,
        imgPaths=imgPaths, imageDir="images",
    )
    return md, absPaths


# ============================================================
# CLI 入口
# ============================================================

def buildArgParser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="MinerU2.5 解析流水线（使用官方 mineru_vl_utils 库）",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
使用步骤：
  1. 先启动 vLLM server:
       bash start_server.sh

  2. 再运行本脚本:
       python parseFlowApi.py input.pdf -o output.md --verbose

示例：
  python parseFlowApi.py doc.pdf -o ./output_dir --verbose
  python parseFlowApi.py scan.png -o ./output_dir --api-url http://192.168.1.100:8000 --verbose
        """
    )
    parser.add_argument("-i", "--input", type=str, help="输入文件（PDF 或图片）")
    parser.add_argument("-o", "--output", type=str, default=None, help="输出目录路径（md文件和images都会放入此目录）")
    parser.add_argument(
        "--api-url", type=str, default="http://192.168.110.208:9091",
        help="vLLM server 地址（默认: http://192.168.110.208:9091）"
    )
    parser.add_argument(
        "--model-name", type=str, default="mineru2.5",
        help="served-model-name（默认: mineru2.5）"
    )
    parser.add_argument("--dpi", type=int, default=300, help="PDF 渲染 DPI（默认 300）")
    parser.add_argument("--keep-meta", action="store_true", help="保留页眉/页脚/页码")
    parser.add_argument("--save-images", action="store_true", help="保存渲染出的页面图片")
    parser.add_argument(
        "--server-timeout", type=int, default=600,
        help="HTTP 超时秒数（默认 600s）"
    )
    parser.add_argument("--verbose", action="store_true", help="打印详细中间过程日志")
    return parser


def main():
    parser = buildArgParser()
    args = parser.parse_args()

    outputDir = args.output or str(Path(args.input).with_suffix(""))

    runPipeline(
        inputPath=args.input,
        outputDir=outputDir,
        apiBaseUrl=args.api_url.rstrip("/"),
        modelName=args.model_name,
        dpi=args.dpi,
        keepMeta=args.keep_meta,
        saveImages=args.save_images,
        serverTimeout=args.server_timeout,
        verbose=args.verbose,
    )


if __name__ == "__main__":
    main()
