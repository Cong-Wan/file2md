# files2MD

`files2MD` 是一个统一的文档转 Markdown 管道，支持 PDF、DOCX 和常见图片格式，并可按需执行图片理解与翻译任务。

## 核心功能

| 功能 | 说明 |
|------|------|
| PDF / 图片解析 | 调用 MinerU API，将 PDF 或图片逐页转换为 Markdown |
| DOCX 解析 | 转换 Word 文档中的文本、表格、公式和图片 |
| 图片理解 | 分析解析结果中的图片，并将结果写回 Markdown |
| 翻译 | 翻译已解析的页面，生成独立的中文 Markdown |
| 断点续传 | 使用 `.cache/progress.json` 保存处理进度，支持从缓存继续执行 |

## 项目结构

```text
files2MD/
├── pipeline.py               # CLI 入口与任务编排
├── core/                     # 管道核心模块
│   ├── __init__.py
│   ├── logUtils.py           # 统一日志工具
│   ├── cacheManager.py       # progress.json 缓存管理
│   ├── imageAnalyzer.py      # 图片理解 API 客户端
│   ├── translator.py         # 翻译 API 客户端
│   ├── parseFlowApi.py       # MinerU 解析库函数
│   └── mdPostprocess.py      # Markdown 与图片后处理
├── docxTools/                # DOCX 解析模块
│   ├── __init__.py
│   ├── constants.py
│   ├── docx_converter.py
│   ├── docx2md.py
│   ├── latex_dict.py
│   ├── magic_model.py
│   ├── markdown_renderer.py
│   ├── middle_json.py
│   └── omml.py
├── docs/                     # 项目文档
├── .env.example              # API 配置示例
├── pyproject.toml            # 项目与依赖配置
├── uv.lock                   # uv 依赖锁文件
└── README.md
```

## 安装与配置

项目使用 [uv](https://docs.astral.sh/uv/) 管理 Python 环境：

```bash
uv sync
cp .env.example .env
```

根据需要在 `.env` 中配置以下变量：

| 变量 | 用途 | 代码默认值 |
|------|------|------------|
| `IMAGE_API_URL` | 图片理解 API 地址 | 空 |
| `IMAGE_API_KEY` | 图片理解 API Key；未配置时跳过图片理解 | 空 |
| `IMAGE_API_MODEL` | 图片理解模型 | `qwen3.5` |
| `PDF_API_URL` | MinerU API 地址 | 空 |
| `PDF_API_MODEL` | MinerU 模型 | `mineru2.5` |
| `TRANSLATE_API_URL` | 翻译 API 地址 | 空 |
| `TRANSLATE_API_KEY` | 翻译 API Key；未配置时尝试使用 `IMAGE_API_KEY` | 空 |
| `TRANSLATE_API_MODEL` | 翻译模型 | `claude-sonnet-4-20250514` |

## 使用方式

统一从 `pipeline.py` 运行：

```bash
# 默认执行 parse,image；输出目录自动创建为 input 同级的 input/
uv run python pipeline.py -i input.pdf --verbose

# 仅解析 PDF
uv run python pipeline.py -i input.pdf --tasks parse --verbose

# 解析并执行图片理解
uv run python pipeline.py -i input.pdf --tasks parse,image --verbose

# 解析、图片理解并翻译
uv run python pipeline.py -i input.pdf --tasks parse,image,translate --verbose

# 指定输出目录（-o 可选）
uv run python pipeline.py -i input.pdf -o ./output --tasks parse --verbose

# 解析 DOCX
uv run python pipeline.py -i input.docx --tasks parse --verbose

# 解析单张图片
uv run python pipeline.py -i input.png --tasks parse,image --verbose
```

`--tasks` 仅支持 `parse`、`image`、`translate`，可用逗号组合；未指定时默认为 `parse,image`。仅执行 `image` 或 `translate` 属于 cache-only 模式，需要沿用原输出目录中的解析缓存：

```bash
uv run python pipeline.py -i input.pdf -o ./output --tasks image --verbose
uv run python pipeline.py -i input.pdf -o ./output --tasks translate --verbose
uv run python pipeline.py -i input.pdf -o ./output --tasks image,translate --verbose
```

## CLI 参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `-i, --input` | 输入文件，支持 PDF、DOCX、PNG、JPG、JPEG、BMP、TIFF、TIF、WEBP | 必填 |
| `-o, --output-dir` | 输出目录（可选） | 输入文件同级、以文件名命名的子目录 |
| `--image-mode` | 图片处理模式：`file`、`base64`、`none` | `file` |
| `--tasks` | 任务列表：`parse`、`image`、`translate`，逗号分隔 | `parse,image` |
| `--dpi` | PDF 渲染 DPI | `300` |
| `--server-timeout` | MinerU API 超时秒数 | `600` |
| `--max-concurrent` | 最大并发图片理解请求数 | `3` |
| `--retry` | 图片理解 API 失败重试次数 | `10` |
| `--retry-delay` | 图片理解重试间隔秒数 | `15.0` |
| `--max-concurrent-translate` | 最大并发翻译请求数 | `5` |
| `--translate-retry` | 翻译失败重试次数 | `10` |
| `--translate-retry-delay` | 翻译重试间隔秒数 | `30.0` |
| `--clean-cache` | 清理缓存并从头开始；必须与 `parse` 任务同用 | `False` |
| `--verbose` | 打印详细日志 | `False` |

`--image-mode none` 会移除 Markdown 中的图片引用并清理图片文件：

```bash
uv run python pipeline.py -i input.pdf --image-mode none --verbose
```

## 输出内容

假设输入文件为 `report.pdf`，默认输出目录为输入文件同级的 `report/`：

```text
report/
├── report.md                 # 主 Markdown 输出
├── report_cn.md              # 启用 translate 时生成
├── images/                   # 提取的图片
└── .cache/
    └── progress.json         # 断点续传缓存
```

## `core/` 模块说明

| 模块 | 职责 |
|------|------|
| `core/__init__.py` | 标记 `core` Python 包 |
| `core/logUtils.py` | 提供统一的分级日志与分隔线输出 |
| `core/cacheManager.py` | 创建、校验和更新 `progress.json`，重建 Markdown |
| `core/imageAnalyzer.py` | 调用图片理解 API，处理并发、重试与响应解析 |
| `core/translator.py` | 调用翻译 API，处理并发、重试与响应解析 |
| `core/parseFlowApi.py` | 提供 MinerU 客户端、输入加载和单页解析库函数 |
| `core/mdPostprocess.py` | 提取图片引用、插入分析结果并应用图片输出模式 |

## `docxTools/` 模块说明

| 模块 | 职责 |
|------|------|
| `docx_converter.py` | DOCX 转换核心实现 |
| `docx2md.py` | DOCX 转换公共接口 |
| `markdown_renderer.py` | Markdown 渲染 |
| `middle_json.py` | 中间结构构建与图片处理 |
| `omml.py` | OMML 数学公式转 LaTeX |
| `magic_model.py` | 文档块分类与关联 |
| `constants.py` / `latex_dict.py` | 常量、枚举与 LaTeX 符号映射 |
