<!-- ============================================================
Author: wilbur
Version: 1.1
  Date: 2026-04-13
  Description: 新增 pipeline 数据管道使用说明
============================================================ -->

# files2MD

基于 MinerU2.5 的 PDF 解析工具，同时支持 DOCX 转 Markdown。

## 核心功能

| 功能 | 说明 |
|------|------|
| PDF 解析 | 调用 MinerU2.5 API，将 PDF/图片转换为 Markdown |
| DOCX 解析 | 解析 Word 文档，支持数学公式、图片、表格等复杂元素 |
| 数据管道 | 统一入口，自动判断文件类型，解析 + 图像理解，一步到位 |

## 项目结构

```
files2MD/
├── pipeline.py               # 统一数据处理管道 CLI 入口
├── imageAnalyzer.py           # 图像理解 API 调用模块
├── parseFlowApi.py            # PDF 解析流水线（调用 MinerU API）
├── cacheManager.py            # 断点续传缓存管理器（progress.json）
├── docxTools/                 # DOCX 解析模块
│   ├── __init__.py
│   ├── constants.py           # 枚举类与常量
│   ├── docx_converter.py      # DOCX 解析核心
│   ├── docx2md.py             # 转换器公共 API
│   ├── latex_dict.py          # LaTeX 符号映射
│   ├── magic_model.py         # 块分类与关联
│   ├── markdown_renderer.py   # Markdown 渲染
│   ├── middle_json.py         # 中间 JSON 构建
│   └── omml.py                # OMML 转 LaTeX
├── requirements.txt           # 项目依赖
└── README.md
```

## 快速开始

### PDF 解析（parseFlowApi.py）

使用 MinerU2.5 API 将 PDF 或图片转换为 Markdown：

```bash
# 基础用法
python parseFlowApi.py input.pdf -o output.md

# 完整示例
python parseFlowApi.py doc.pdf -o out.md --api-url http://192.168.110.208:9091 --verbose
```

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i, --input` | 输入文件（PDF 或图片） | 必填 |
| `-o, --output` | 输出 Markdown 路径 | `<输入文件>.md` |
| `--api-url` | vLLM server 地址 | `http://192.168.110.208:9091` |
| `--model-name` | served-model-name | `mineru2.5` |
| `--dpi` | PDF 渲染 DPI | `300` |
| `--keep-meta` | 保留页眉/页脚/页码 | `False` |
| `--save-images` | 保存渲染出的页面图片 | `False` |
| `--verbose` | 打印详细日志 | `False` |

**前置要求：**
1. 启动 vLLM server（在 208 机器上）：
   ```bash
   vllm serve /home/Disk-6T/models/MinerU2.5-2509-1.2B \
     --host 0.0.0.0 --port 9091 \
     --gpu-memory-utilization 0.85 \
     --max-model-len 16384 --dtype bfloat16 \
     --trust-remote-code \
     --limit-mm-per-prompt '{"image": 8}' \
     --served-model-name "mineru2.5" \
     --logits-processors mineru_vl_utils:MinerULogitsProcessor
   ```
2. 安装依赖：`pip install pypdfium2 mineru_vl_utils Pillow`

---

### DOCX 解析（docxTools）

```python
from docxTools import DocxToMarkdownConverter

# 初始化转换器
converter = DocxToMarkdownConverter()

# 转换 DOCX 文件
result = converter.convert_file("input.docx")

# 输出 Markdown
print(result.markdown)
```

**安装依赖：**
```bash
pip install python-docx mammoth lxml pandas Pillow loguru pydantic pylatexenc beautifulsoup4
```

### 数据管道（pipeline.py）

统一的 CLI 入口，自动判断文件类型，走对应解析路径，并对图片调用图像理解 API：

```bash
# 仅解析 PDF
python pipeline.py -i input.pdf -o ./output --tasks parse --verbose

# 解析 PDF + 图片理解
python pipeline.py -i input.pdf -o ./output --tasks parse,image --verbose

# 解析 PDF + 翻译
python pipeline.py -i input.pdf -o ./output --tasks parse,translate --verbose

# 解析 PDF + 图片理解 + 翻译
python pipeline.py -i input.pdf -o ./output --tasks parse,image,translate --verbose

# 解析完成后，仅补做图片理解
python pipeline.py -i input.pdf -o ./output --tasks image --verbose

# 解析完成后，仅补做翻译
python pipeline.py -i input.pdf -o ./output --tasks translate --verbose

# 解析完成后，补做图片理解和翻译
python pipeline.py -i input.pdf -o ./output --tasks image,translate --verbose

# 解析 DOCX
python pipeline.py -i input.docx -o ./output --verbose

# 仅保留分析结果，不保留原图
python pipeline.py -i input.pdf -o ./output --image-mode none --verbose

# 强制从头开始（清理缓存）
python pipeline.py -i input.pdf --clean-cache --verbose
```

**断点续传：** 管道默认开启增量模式，已处理的页会自动跳过，中断后可从断点恢复。

**参数说明：**

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i, --input` | 输入文件 (PDF/DOCX/Image) | 必填 |
| `-o, --output-dir` | 输出目录 | 必填 |
| `--image-mode` | 图片处理模式 (file/base64/none) | `file` |
| `--tasks` | 显式任务列表：`parse`、`image`、`translate` 可组合 | `parse,image` |
| `--skip-image-analysis` | 兼容参数，等价于 `--tasks parse` | `False` |
| `--reanalyze-images` | 兼容参数，等价于 `--tasks image` | `False` |
| `--enable-translation` | 兼容参数，在默认任务上追加 `translate` | `False` |
| `--clean-cache` | 清理缓存，强制从头开始 | `False` |
| `--image-api-url` | 图像理解 API 地址 | `http://192.168.0.101:10101/v1/chat/completions` |
| `--image-api-key` | 图像理解 API Key | 环境变量 `IMAGE_API_KEY` |
| `--max-concurrent` | 最大并发请求数 | `3` |
| `--retry` | API 失败重试次数 | `3` |
| `--verbose` | 详细日志 | `False` |

## 模块说明

### parseFlowApi.py（PDF 解析）

调用 `mineru_vl_utils` 库的 `MinerUClient`，通过两阶段提取（two_step_extract）将 PDF/图片转为 Markdown：

1. 加载输入（PDF 渲染或图片加载）
2. 逐页调用 API 解析
3. 裁剪并保存 image block
4. 组装 Markdown 输出

### docxTools（DOCX 解析）

| 模块 | 功能 |
|------|------|
| `docx_converter.py` | DOCX 文档解析核心类 |
| `markdown_renderer.py` | Markdown 渲染函数 |
| `middle_json.py` | 中间 JSON 构建与图片处理 |
| `omml.py` | OMML 数学公式转 LaTeX |
| `magic_model.py` | 块分类与文本解析 |
| `latex_dict.py` | LaTeX 数学符号映射字典 |

