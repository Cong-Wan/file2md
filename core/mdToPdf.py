r'''
Author: wilbur
Version: 1.1
Date: 2026-08-03
Description: Markdown -> PDF 渲染模块（pipeline --tasks pdf 的核心）。
             引擎：xhtml2pdf（纯 Python，基于 reportlab），无系统依赖。
             公式：matplotlib mathtext 渲染 PNG（复杂宏回退等宽文本）。
             CJK：Arial Unicode.ttf；代码块 font-family 含 CJK 回退。
             图片：link_callback 解析相对路径，base_url=imageBaseDir。
Version 1.1: 扩展公式预处理覆盖 4 种格式（$$...$$ / $...$ / \(...\) / \[...\]，
             MinerU 输出格式因论文而异）；新增 sanitizeTables 空单元格填充修复
             xhtml2pdf KeepInFrame 负宽度崩溃；CSS 加 table-layout:fixed。
'''

import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import markdown
from xhtml2pdf import pisa

from core.logUtils import log


# macOS 通用 CJK 字体（Arial Unicode 覆盖中英日韩）
CJK_FONT = "/Library/Fonts/Arial Unicode.ttf"

CSS_TEMPLATE = """
@page {{ size: A4; margin: 2cm; }}
@font-face {{ font-family: 'CJK'; src: url('{cjkFont}'); }}
body {{ font-family: 'CJK'; font-size: 11pt; line-height: 1.6; color: #222; }}
h1 {{ font-size: 20pt; color: #111; margin: 14pt 0 8pt; }}
h2 {{ font-size: 16pt; color: #111; margin: 12pt 0 6pt; }}
h3 {{ font-size: 13pt; color: #222; margin: 10pt 0 4pt; }}
p {{ margin: 6pt 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 8pt 0; table-layout: fixed; }}
th, td {{ border: 1px solid #999999; padding: 5pt; text-align: left; }}
img {{ max-width: 100%; }}
pre {{ font-family: 'CJK', 'Courier'; background-color: #f5f5f5; padding: 8pt;
      white-space: pre-wrap; font-size: 9pt; }}
code {{ font-family: 'CJK', 'Courier'; }}
"""


# ============================================================
# 代码段保护（公式预处理时跳过代码块/行内代码，避免误伤 $x$ 等）
# ============================================================

def stripCodeSpans(md: str) -> tuple[str, dict]:
    """切出 fenced code block 和 inline code，占位替换。

    返回 (占位后的 md, {占位: 原文})，后续用 restoreCodeSpans 还原。
    """
    spans = {}
    counter = [0]

    def repl(m):
        key = f"@@CODESPAN_{counter[0]}@@"
        spans[key] = m.group(0)
        counter[0] += 1
        return key

    md = re.sub(r"```.*?```", repl, md, flags=re.DOTALL)
    md = re.sub(r"`[^`]+`", repl, md)
    return md, spans


def restoreCodeSpans(md: str, spans: dict) -> str:
    for k, v in spans.items():
        md = md.replace(k, v)
    return md


# ============================================================
# 公式预处理：$$...$$ / $...$ -> matplotlib PNG -> <img>
# ============================================================

def renderEqPng(eq: str, outPath: str) -> bool:
    """matplotlib mathtext 渲染公式到 PNG 文件。失败返回 False。"""
    fig = plt.figure(figsize=(0.01, 0.01))
    try:
        fig.text(0, 0, f"${eq}$", fontsize=14)
        fig.savefig(outPath, format="png", dpi=150, bbox_inches="tight",
                    pad_inches=0.1, transparent=True)
        plt.close(fig)
        return True
    except Exception:
        plt.close(fig)
        return False


def preprocessMathPng(md: str, imgDir: str, verbose: bool) -> str:
    r"""扫描 4 种公式定界符，渲染为 PNG 文件并替换为 <img>。失败回退 <pre>/<code>。

    覆盖 MinerU 输出的全部格式：
      块级：$$...$$ 与 \[...\]
      行内：$...$ 与 \(...\)
    """
    os.makedirs(imgDir, exist_ok=True)
    counter = [0]

    def replBlock(m):
        eq = m.group(1).strip()
        fname = f"_formula_block_{counter[0]}.png"
        counter[0] += 1
        if renderEqPng(eq, os.path.join(imgDir, fname)):
            log(f"公式渲染(block): {eq[:40]}", "DEBUG", verbose, tag="[mdToPdf]")
            return f'<img src="{fname}" />'
        log(f"公式渲染失败，回退文本(block): {eq[:40]}", "WARN", verbose, tag="[mdToPdf]")
        return f"<pre>{eq}</pre>"

    def replInline(m):
        eq = m.group(1).strip()
        fname = f"_formula_inline_{counter[0]}.png"
        counter[0] += 1
        if renderEqPng(eq, os.path.join(imgDir, fname)):
            return f'<img src="{fname}" height="14" />'
        log(f"公式渲染失败，回退文本(inline): {eq[:40]}", "WARN", verbose, tag="[mdToPdf]")
        return f"<code>{eq}</code>"

    # 块级（跨行）
    md = re.sub(r"\$\$(.+?)\$\$", replBlock, md, flags=re.DOTALL)
    md = re.sub(r"\\\[(.+?)\\\]", replBlock, md, flags=re.DOTALL)
    # 行内（不跨行）；$...$ 用 lookbehind/lookahead 避免误匹配 $$ 残留
    md = re.sub(r"(?<!\$)\$([^$\n]+?)\$(?!\$)", replInline, md)
    md = re.sub(r"\\\((.+?)\\\)", replInline, md)
    return md


# ============================================================
# 分页符 + md->html
# ============================================================

def applyPageBreak(md: str) -> str:
    """rebuild 的每页分隔 --- 转成真正分页符，避免 PDF 中出现水平线。仅作用于 pdf 输入。"""
    return md.replace("\n\n---\n\n", '\n\n<div style="page-break-after:always"></div>\n\n')


def mdToHtml(md: str) -> str:
    return markdown.markdown(md, extensions=["tables", "fenced_code"])


def sanitizeTables(html: str) -> str:
    """空 td/th 填充 &nbsp;，修复 xhtml2pdf 对空单元格 KeepInFrame 宽度计算为负的崩溃。"""
    html = re.sub(r"<td>\s*</td>", "<td>&nbsp;</td>", html, flags=re.IGNORECASE)
    html = re.sub(r"<th>\s*</th>", "<th>&nbsp;</th>", html, flags=re.IGNORECASE)
    return html


# ============================================================
# 渲染 PDF
# ============================================================

def renderPdf(html: str, pdfPath: str, baseDir: str, verbose: bool) -> bool:
    """xhtml2pdf 渲染 HTML -> PDF。link_callback 解析相对路径图片。"""
    css = CSS_TEMPLATE.format(cjkFont=CJK_FONT)
    full = (f"<html><head><meta charset='utf-8'><style>{css}</style></head>"
            f"<body>{html}</body></html>")

    def linkCb(uri, rel):
        if (os.path.isabs(uri) or uri.startswith(("http://", "https://", "file://", "data:"))):
            return uri
        return os.path.join(baseDir, uri)

    os.makedirs(os.path.dirname(pdfPath) or ".", exist_ok=True)
    with open(pdfPath, "wb") as f:
        result = pisa.CreatePDF(full, dest=f, path=baseDir, link_callback=linkCb, encoding="utf-8")
    if result.err:
        log(f"PDF 渲染产生 {result.err} 个错误: {pdfPath}", "WARN", verbose, tag="[mdToPdf]")
        return False
    log(f"PDF 已生成: {pdfPath}", "INFO", verbose, tag="[mdToPdf]")
    return True


# ============================================================
# 主入口
# ============================================================

def markdownToPdf(mdText: str, pdfPath: str, imageBaseDir: str, verbose: bool = False) -> bool:
    """将 Markdown 文本渲染为 PDF。

    参数:
        mdText: Markdown 文本（译文或原文，来自 cache 内存）
        pdfPath: 输出 PDF 绝对路径
        imageBaseDir: 图片基准目录（解析 images/xxx.png 相对路径，公式 PNG 也存此目录）
        verbose: 是否输出详细日志

    返回:
        True 渲染成功，False 失败
    """
    if not mdText or not mdText.strip():
        log("mdText 为空，跳过 PDF 渲染", "WARN", verbose, tag="[mdToPdf]")
        return False

    log(f"开始渲染 PDF: {pdfPath}", "INFO", verbose, tag="[mdToPdf]")

    # 1. 分页符
    md = applyPageBreak(mdText)
    # 2. 切出代码段（公式预处理不碰代码）
    md, spans = stripCodeSpans(md)
    # 3. 公式 -> PNG
    md = preprocessMathPng(md, imageBaseDir, verbose)
    # 4. 还原代码段
    md = restoreCodeSpans(md, spans)
    # 5. md -> html
    html = mdToHtml(md)
    # 5.1 表格空单元格填充（修复 xhtml2pdf 崩溃）
    html = sanitizeTables(html)
    # 6. html -> pdf
    return renderPdf(html, pdfPath, imageBaseDir, verbose)
