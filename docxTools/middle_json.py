'''
Author: wilbur
Version: 1.0
  Date: 2026-04-10
  Description: Middle JSON 构建函数 + _MemoryImageWriter
'''

import base64
import re
from collections import defaultdict

from loguru import logger

from .constants import _BlockType
from .magic_model import _MagicModel
from .docx_converter import _str_sha256


class _MemoryImageWriter:
    """In-memory image writer that discards all writes (for base64 mode)."""

    def write(self, path: str, data: bytes) -> None:
        pass  # intentionally a no-op


def _save_base64_image(b64_data_uri: str, image_writer, page_index: int):
    """将 data-URI 格式的 base64 图片解码并通过 image_writer 保存到本地。

    Args:
        b64_data_uri: 形如 ``data:image/{fmt};base64,{data}`` 的字符串。
        image_writer:  DataWriter 实例，用于将字节写入本地存储。
        page_index:    当前页索引，仅用于日志信息。

    Returns:
        保存成功时返回相对路径字符串（如 ``"abc123.png"``），否则返回 ``None``。
    """
    m = re.match(r'data:image/(\w+);base64,(.+)', b64_data_uri, re.DOTALL)
    if not m:
        logger.warning(f"Unrecognized image_base64 format in page {page_index}, skipping.")
        return None
    fmt = m.group(1)
    ext = "jpg" if fmt == "jpeg" else fmt
    try:
        img_bytes = base64.b64decode(m.group(2))
    except Exception as e:
        logger.warning(f"Failed to decode image_base64 on page {page_index}: {e}")
        return None
    img_path = f"{_str_sha256(b64_data_uri)}.{ext}"
    image_writer.write(img_path, img_bytes)
    return img_path


def _save_span_image_if_needed(span: dict, image_writer, page_index: int) -> None:
    """Persist a span-level base64 image and normalize the image_path field.

    When *image_writer* is a ``_MemoryImageWriter`` (base64 mode), the image
    is **not** written to disk.  The ``image_path`` is still set (so downstream
    code can reference it), but ``image_base64`` is kept intact so the
    renderer can embed it as a data-URI.
    """
    img_b64 = span.get("image_base64", "")
    if img_b64:
        # In base64 mode we keep the raw data-URI for inline embedding
        if isinstance(image_writer, _MemoryImageWriter):
            m = re.match(r'data:image/(\w+);base64,.+', img_b64, re.DOTALL)
            if m:
                fmt = m.group(1)
                ext = "jpg" if fmt == "jpeg" else fmt
                span["image_path"] = f"{_str_sha256(img_b64)}.{ext}"
            else:
                span.setdefault("image_path", "")
            # Do NOT delete image_base64 — renderer needs it for data-URI
            return
        img_path = _save_base64_image(img_b64, image_writer, page_index)
        if img_path:
            span["image_path"] = img_path
            del span["image_base64"]
            return
    span.setdefault("image_path", "")


def _replace_inline_base64_img_src(markup: str, image_writer, page_index: int) -> str:
    """Replace inline base64 image sources in HTML-like markup with saved local paths.

    In base64 mode (``_MemoryImageWriter``) the original markup is returned
    unchanged so that data-URIs stay inline.
    """
    if not markup or "base64," not in markup:
        return markup

    # In base64 mode keep the data-URI as-is
    if isinstance(image_writer, _MemoryImageWriter):
        return markup

    def _replace_src(m_src, _writer=image_writer, _idx=page_index):
        img_path = _save_base64_image(m_src.group(1), _writer, _idx)
        if img_path:
            return f'src="{img_path}"'
        return m_src.group(0)

    return re.sub(
        r'src="(data:image/[^"]+)"',
        _replace_src,
        markup,
    )


def _blocks_to_page_info(page_blocks, image_writer, page_index) -> dict:
    """将blocks转换为页面信息"""

    magic_model = _MagicModel(page_blocks)
    image_blocks = magic_model.get_image_blocks()
    table_blocks = magic_model.get_table_blocks()
    chart_blocks = magic_model.get_chart_blocks()

    if image_writer:

        # Write embedded images to local storage via image_writer
        for img_block in image_blocks:
            for sub_block in img_block.get("blocks", []):
                if sub_block.get("type") != "image_body":
                    continue
                for line in sub_block.get("lines", []):
                    for span in line.get("spans", []):
                        _save_span_image_if_needed(span, image_writer, page_index)

        # Replace inline base64 images inside table HTML with local paths
        for tbl_block in table_blocks:
            for sub_block in tbl_block.get("blocks", []):
                if sub_block.get("type") != "table_body":
                    continue
                for line in sub_block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("type") != "table":
                            continue
                        span["html"] = _replace_inline_base64_img_src(
                            span.get("html", ""),
                            image_writer,
                            page_index,
                        )

        # Replace inline base64 images inside chart content with local paths
        for chart_block in chart_blocks:
            for sub_block in chart_block.get("blocks", []):
                if sub_block.get("type") != "chart_body":
                    continue
                for line in sub_block.get("lines", []):
                    for span in line.get("spans", []):
                        if span.get("type") != "chart":
                            continue
                        _save_span_image_if_needed(span, image_writer, page_index)

    title_blocks = magic_model.get_title_blocks()
    discarded_blocks = magic_model.get_discarded_blocks()
    list_blocks = magic_model.get_list_blocks()
    index_blocks = magic_model.get_index_blocks()
    text_blocks = magic_model.get_text_blocks()
    interline_equation_blocks = magic_model.get_interline_equation_blocks()

    page_blocks = []
    page_blocks.extend([
        *image_blocks,
        *chart_blocks,
        *table_blocks,
        *title_blocks,
        *text_blocks,
        *interline_equation_blocks,
        *list_blocks,
        *index_blocks,
    ])
    # 对page_blocks根据index的值进行排序
    page_blocks.sort(key=lambda x: x["index"])

    page_info = {"para_blocks": page_blocks, "discarded_blocks": discarded_blocks, "page_idx": page_index}
    return page_info


def _extract_section_parts_from_content(content: str, level: int):
    """Try to extract a leading section number (e.g. '1.2.1') from title content.

    Returns a list of ints [n1, n2, ..., nL] when the number of parts equals
    `level`, otherwise None.  Handles formats like:
        '1心肌特异性...'       (no separator)
        '1.2.1建立...'         (Chinese text immediately after number)
        '2.2.1 ALKBH5 ...'    (space separator)
    """
    match = re.match(r'^(\d+(?:\.\d+)*)', content.strip())
    if match:
        parts = [int(p) for p in match.group(1).split('.')]
        if len(parts) == level:
            return parts
    return None


def _collect_index_text_blocks(index_block: dict, result: list[dict]) -> None:
    """Depth-first collect TOC leaf text blocks."""
    for child in index_block.get("blocks", []):
        if child.get("type") == _BlockType.INDEX:
            _collect_index_text_blocks(child, result)
        elif child.get("type") == _BlockType.TEXT:
            result.append(child)


def _link_index_entries_by_anchor(middle_json: dict) -> None:
    """Keep TOC anchors only when they exist on parsed body blocks."""
    pdf_info = middle_json.get("pdf_info", [])
    valid_anchors: set[str] = set()

    for page_info in pdf_info:
        for block in page_info.get("para_blocks", []):
            anchor = block.get("anchor")
            if isinstance(anchor, str) and anchor.strip():
                valid_anchors.add(anchor.strip())

    if not valid_anchors:
        return

    for page_info in pdf_info:
        for block in page_info.get("para_blocks", []):
            if block.get("type") != _BlockType.INDEX:
                continue
            toc_text_blocks: list[dict] = []
            _collect_index_text_blocks(block, toc_text_blocks)
            for text_block in toc_text_blocks:
                anchor = text_block.get("anchor")
                if not isinstance(anchor, str):
                    text_block.pop("anchor", None)
                    continue
                anchor = anchor.strip()
                if not anchor or anchor not in valid_anchors:
                    text_block.pop("anchor", None)
                    continue
                text_block["anchor"] = anchor


def _result_to_middle_json(model_output_blocks_list, image_writer):
    middle_json = {"pdf_info": [], "_backend":"office", "_version_name": 'standalone'}
    for index, page_blocks in enumerate(model_output_blocks_list):
        page_info = _blocks_to_page_info(page_blocks, image_writer, index)
        middle_json["pdf_info"].append(page_info)

    section_counters: dict[int, int] = defaultdict(int)
    for page_info in middle_json["pdf_info"]:
        for block in page_info.get("para_blocks", []):
            if block.get("type") != _BlockType.TITLE:
                continue
            level = block.get("level", 1)
            if block.get("is_numbered_style", False):
                # Ensure all ancestor levels start at 1 (never 0)
                for ancestor in range(1, level):
                    if section_counters[ancestor] == 0:
                        section_counters[ancestor] = 1
                # Increment current level counter and reset all deeper levels
                section_counters[level] += 1
                for deeper in list(section_counters.keys()):
                    if deeper > level:
                        section_counters[deeper] = 0
                # Build section number string, e.g. "1.2.1."
                section_number = ".".join(
                    str(section_counters[l]) for l in range(1, level + 1)
                ) + "."
                block["section_number"] = section_number
            else:
                # Some documents embed the section number directly in the content
                # (is_numbered_style=False).  Parse it and sync the counters so
                # that subsequent numbered blocks continue from the right base.
                lines = block.get("lines", [])
                content = ""
                if lines and lines[0].get("spans"):
                    content = lines[0]["spans"][0].get("content", "")
                parts = _extract_section_parts_from_content(content, level)
                if parts:
                    for k, v in enumerate(parts, start=1):
                        section_counters[k] = v
                    # Reset all deeper levels
                    for deeper in list(section_counters.keys()):
                        if deeper > level:
                            section_counters[deeper] = 0

    _link_index_entries_by_anchor(middle_json)
    return middle_json
