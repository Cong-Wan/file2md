'''
Author: wilbur
Version: 1.1
Date: 2026-08-03
Description: md->pdf demo（纯 Python，xhtml2pdf + matplotlib PNG 公式）。
             v1.0 用 weasyprint，因 pango/glib 系统依赖源码编译过慢弃用；
             v1.1 改用 xhtml2pdf（纯 Python），公式仅 matplotlib PNG 路径（xhtml2pdf 不支持 MathML）。
             覆盖：中文CJK、表格、图片、简单公式、align复杂公式(预期回退文本)、代码块(含$不误渲染)、分页符。
'''

import os
import re

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import markdown
from xhtml2pdf import pisa


CJK_FONT = "/Library/Fonts/Arial Unicode.ttf"

CSS = f"""
@page {{ size: A4; margin: 2cm; }}
@font-face {{ font-family: 'CJK'; src: url('{CJK_FONT}'); }}
body {{ font-family: 'CJK'; font-size: 12pt; line-height: 1.6; color: #222; }}
h1 {{ font-size: 20pt; color: #111; }}
h2 {{ font-size: 16pt; color: #111; }}
p {{ margin: 6pt 0; }}
table {{ border-collapse: collapse; width: 100%; margin: 8pt 0; }}
th, td {{ border: 1px solid #999999; padding: 5pt; text-align: left; }}
img {{ max-width: 100%; }}
pre {{ font-family: 'CJK', 'Courier'; background-color: #f5f5f5; padding: 8pt;
      white-space: pre-wrap; font-size: 10pt; }}
code {{ font-family: 'CJK', 'Courier'; }}
"""


def stripCodeSpans(md):
    """切出 fenced code block 和 inline code，占位替换。"""
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


def restoreCodeSpans(md, spans):
    for k, v in spans.items():
        md = md.replace(k, v)
    return md


def renderEqPng(eq, outPath):
    """matplotlib mathtext 渲染公式到文件；失败返回 False"""
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


def preprocessMathPng(md, imgDir):
    """$$...$$ / $...$ -> <img> 引用 PNG 文件（matplotlib）"""
    counter = [0]

    def replBlock(m):
        eq = m.group(1).strip()
        fname = f"formula_block_{counter[0]}.png"
        counter[0] += 1
        if renderEqPng(eq, os.path.join(imgDir, fname)):
            return f'<img src="{fname}" />'
        return f"<pre>{eq}</pre>"

    def replInline(m):
        eq = m.group(1).strip()
        fname = f"formula_inline_{counter[0]}.png"
        counter[0] += 1
        if renderEqPng(eq, os.path.join(imgDir, fname)):
            return f'<img src="{fname}" height="14" />'
        return f"<code>{eq}</code>"

    md = re.sub(r"\$\$(.+?)\$\$", replBlock, md, flags=re.DOTALL)
    md = re.sub(r"\$([^$\n]+?)\$", replInline, md)
    return md


def pageBreak(md):
    return md.replace("\n\n---\n\n", '\n\n<div style="page-break-after:always"></div>\n\n')


def mdToHtml(md):
    return markdown.markdown(md, extensions=["tables", "fenced_code"])


def renderPdf(html, pdfPath, baseDir):
    full = (f"<html><head><meta charset='utf-8'><style>{CSS}</style></head>"
            f"<body>{html}</body></html>")

    def linkCb(uri, rel):
        # 相对路径 -> baseDir 下的绝对路径；绝对/file/data/http 原样返回
        if (os.path.isabs(uri) or uri.startswith(("http://", "https://", "file://", "data:"))):
            return uri
        return os.path.join(baseDir, uri)

    with open(pdfPath, "wb") as f:
        result = pisa.CreatePDF(full, dest=f, path=baseDir, link_callback=linkCb, encoding="utf-8")
    if result.err:
        print(f"[WARN] pisa 产生 {result.err} 个错误")
    return result


TEST_MD = """# 公式渲染测试

## 1. 中文正文与图片

这是一段中文正文，用于验证 CJK 字体。含图片引用：

![示例图片](test.png)

## 2. 表格

| 名称 | 公式 | 说明 |
|---|---|---|
| 勾股定理 | $a^2+b^2=c^2$ | 直角三角形 |
| 求根公式 | $x=\\frac{-b\\pm\\sqrt{b^2-4ac}}{2a}$ | 一元二次 |

## 3. 简单公式（块级）

$$\\int_0^1 x^2 dx = \\frac{1}{3}$$

## 4. 复杂公式（align 环境，预期回退文本）

$$\\begin{align} a &= b+c \\\\ d &= e-f \\end{align}$$

## 5. 代码块（含 $ 不应被渲染）

```python
def f(x):
    return x**2  # $x$ 在代码里不应被渲染
```

行内代码 `$x$` 也不应被渲染。

---

# 第二页（分页符测试）
"""


def main():
    outDir = "/Users/wilbur/project/files2MD/scripts/demo_out"
    os.makedirs(outDir, exist_ok=True)
    # 造一张测试图
    plt.figure(figsize=(2, 1))
    plt.plot([0, 1, 2], [0, 1, 0])
    plt.title("test")
    plt.savefig(os.path.join(outDir, "test.png"), dpi=80)
    plt.close()

    md = pageBreak(TEST_MD)
    md, spans = stripCodeSpans(md)
    md = preprocessMathPng(md, outDir)
    md = restoreCodeSpans(md, spans)
    html = mdToHtml(md)
    pdfPath = os.path.join(outDir, "demo.pdf")
    renderPdf(html, pdfPath, outDir)
    print(f"[OK] demo 生成: {pdfPath}")
    print(f"  公式 PNG: {[f for f in os.listdir(outDir) if f.startswith('formula')]}")


if __name__ == "__main__":
    main()
