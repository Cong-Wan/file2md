'''
Author: wilbur
Version: 1.0
  Date: 2026-04-10
  Description: docxTools 包统一导出
'''

from .constants import (
    _BlockType, _ContentType, _ContentTypeV2, _MakeMode, _Script,
    _Formatting,
)
from .docx_converter import _DocxConverter
from .magic_model import (
    _MagicModel, classify_caption_blocks, fix_two_layer_blocks,
    parse_list_block, parse_index_block, parse_text_block_spans,
)
from .middle_json import _result_to_middle_json
from .markdown_renderer import (
    _union_make, merge_para_with_text,
    merge_list_to_markdown, merge_index_to_markdown,
)

from .docx2md import DocxToMarkdownConverter

__all__ = [
    'DocxToMarkdownConverter', '_DocxConverter', '_MagicModel',
    '_BlockType', '_ContentType', '_ContentTypeV2', '_MakeMode', '_Script',
    '_Formatting',
    '_union_make', '_result_to_middle_json',
    'classify_caption_blocks', 'fix_two_layer_blocks',
    'parse_list_block', 'parse_index_block', 'parse_text_block_spans',
    'merge_para_with_text', 'merge_list_to_markdown', 'merge_index_to_markdown',
]
