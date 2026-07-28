'''
Author: wilbur
Version: 1.0
Date: 2026-07-28
Description: Markdown 后处理工具：图片引用提取、图像分析结果插入与重建、image-mode 处理
'''

import os
import re
import shutil

from core.logUtils import log


# Markdown 图片引用正则
IMAGE_REF_PATTERN = re.compile(r'(!\[[^\]]*\]\(([^)]+)\))')
# Group 1: 完整的 ![alt](src)
# Group 2: 图片路径或 data URI


def guessImageFormat(path: str) -> str:
    """根据路径后缀推断图片格式（png/jpeg/webp）。"""
    lower = path.lower()
    if lower.endswith((".jpg", ".jpeg")):
        return "jpeg"
    if lower.endswith(".webp"):
        return "webp"
    return "png"


def isDataUri(src: str) -> bool:
    """判断是否为 data URI。"""
    return src.startswith("data:image/")


def extractImageSources(markdown: str, outputDir: str, verbose: bool) -> list[tuple[int, dict]]:
    """从 Markdown 中提取所有图片引用及其 source 信息。

    返回: [(行索引, imageSource dict), ...]
    使用 finditer 支持同一行中的多个图片引用。
    """
    lines = markdown.split("\n")
    sources = []
    for lineIdx, line in enumerate(lines):
        for match in IMAGE_REF_PATTERN.finditer(line):
            src = match.group(2)
            if isDataUri(src):
                # data:image/png;base64,xxxxx
                headerEnd = src.index(",") + 1
                base64Data = src[headerEnd:]
                # 推断格式
                fmt = "png"
                if "jpeg" in src or "jpg" in src:
                    fmt = "jpeg"
                elif "webp" in src:
                    fmt = "webp"
                sources.append((lineIdx, {"base64": base64Data, "format": fmt}))
            else:
                # 文件路径（相对路径）
                absPath = os.path.normpath(os.path.join(outputDir, src))
                sources.append((lineIdx, {"path": absPath, "format": guessImageFormat(src)}))

    log(f"发现 {len(sources)} 个图片引用", "INFO", verbose)
    return sources


def buildAnalysisBlock(result: dict) -> str:
    """将图像分析结果构建为 Markdown 文本块。"""
    imgType = result.get("type", "document")
    content = result.get("content", "")
    summary = result.get("summary", "")

    lines = []

    if imgType == "flowchart":
        # 流程图：summary + mermaid 代码块
        header = f"> **[图像分析 - 流程图]** {summary}" if summary else "> **[图像分析 - 流程图]**"
        lines.append(header)
        lines.append(">")
        mermaidLines = content.split("\n")
        lines.append("> ```mermaid")
        for ml in mermaidLines:
            lines.append(f"> {ml}")
        lines.append("> ```")
    else:
        # 文档/图表：summary + content
        header = f"> **[图像分析]** {summary}" if summary else "> **[图像分析]**"
        lines.append(header)
        if content:
            lines.append(">")
            for contentLine in content.split("\n"):
                lines.append(f"> {contentLine}")

    return "\n".join(lines)


def insertAnalysisResults(
    markdown: str,
    outputDir: str,
    analysisResults: dict,
    verbose: bool,
    indexBased: bool = False,
    indexResults: list | None = None,
) -> str:
    """将预计算的图像分析结果插入 Markdown。"""
    imageSources = extractImageSources(markdown, outputDir, verbose)
    if not imageSources:
        return markdown

    lines = markdown.split("\n")
    insertions = []
    for i, (lineIdx, src) in enumerate(imageSources):
        if indexBased and indexResults is not None:
            result = indexResults[i] if i < len(indexResults) else None
        else:
            absPath = src.get("path")
            result = analysisResults.get(absPath) if absPath else None
        if result is not None:
            block = buildAnalysisBlock(result)
            insertions.append((lineIdx, block))
            log(f"图片[{i}] 分析成功: type={result.get('type')}", "DEBUG", verbose)
        else:
            log(f"图片[{i}] 无分析结果，保留原始图片引用", "WARN", verbose)

    # 倒序插入
    for lineIdx, block in sorted(insertions, key=lambda x: x[0], reverse=True):
        lines.insert(lineIdx, block + "\n")

    return "\n".join(lines)


def buildFinalMarkdownFromCache(pageData: dict, outputDir: str, verbose: bool) -> str:
    """从缓存中的页数据生成包含分析结果的最终 markdown。"""
    rawMd = pageData["rawMarkdown"]
    images = pageData.get("images", [])

    if not images:
        return rawMd

    analysisResults = {}
    for img in images:
        absPath = img.get("absPath")
        if absPath and img.get("analysisResult") is not None:
            analysisResults[absPath] = img["analysisResult"]

    if analysisResults:
        return insertAnalysisResults(rawMd, outputDir, analysisResults, verbose)
    return rawMd


def applyImageMode(markdown: str, outputDir: str, imageMode: str,
                   verbose: bool) -> str:
    """根据 image-mode 处理最终输出。"""
    if imageMode == "base64":
        log("image-mode=base64: 尚未实现，保留图片引用不变", "WARN", verbose)
    elif imageMode == "none":
        log("image-mode=none: 移除图片引用并清理图片文件", "STEP", verbose)
        lines = markdown.split("\n")
        filtered = []
        for line in lines:
            if IMAGE_REF_PATTERN.search(line):
                log(f"  移除图片引用: {line.strip()[:80]}", "DEBUG", verbose)
                continue
            filtered.append(line)
        markdown = "\n".join(filtered)

        imagesDir = os.path.join(outputDir, "images")
        if os.path.isdir(imagesDir):
            shutil.rmtree(imagesDir)
            log(f"  已删除图片目录: {imagesDir}", "INFO", verbose)

    return markdown
