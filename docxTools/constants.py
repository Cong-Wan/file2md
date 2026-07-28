'''
Author: wilbur
Version: 1.0
  Date: 2026-04-10
  Description: 枚举类与 Block/Content 类型常量
'''

from enum import Enum
from typing import Literal
from pydantic import BaseModel


class _Script(str, Enum):
    BASELINE = 'baseline'
    SUB = 'sub'
    SUPER = 'super'


class _Formatting(BaseModel):
    bold: bool = False
    italic: bool = False
    underline: bool = False
    strikethrough: bool = False
    script: _Script = _Script.BASELINE


class _BlockType:
    IMAGE = 'image'
    TABLE = 'table'
    CHART = 'chart'
    IMAGE_BODY = 'image_body'
    TABLE_BODY = 'table_body'
    CHART_BODY = 'chart_body'
    CAPTION = 'caption'
    IMAGE_CAPTION = 'image_caption'
    TABLE_CAPTION = 'table_caption'
    CHART_CAPTION = 'chart_caption'
    FOOTNOTE = 'footnote'
    IMAGE_FOOTNOTE = 'image_footnote'
    TABLE_FOOTNOTE = 'table_footnote'
    TEXT = 'text'
    TITLE = 'title'
    INTERLINE_EQUATION = 'interline_equation'
    EQUATION = 'equation'
    LIST = 'list'
    INDEX = 'index'
    DISCARDED = 'discarded'
    CODE = 'code'
    CODE_BODY = 'code_body'
    CODE_CAPTION = 'code_caption'
    CODE_FOOTNOTE = 'code_footnote'
    ALGORITHM = 'algorithm'
    REF_TEXT = 'ref_text'
    PHONETIC = 'phonetic'
    HEADER = 'header'
    FOOTER = 'footer'
    PAGE_NUMBER = 'page_number'
    ASIDE_TEXT = 'aside_text'
    PAGE_FOOTNOTE = 'page_footnote'


class _ContentType:
    IMAGE = 'image'
    TABLE = 'table'
    CHART = 'chart'
    TEXT = 'text'
    INTERLINE_EQUATION = 'interline_equation'
    INLINE_EQUATION = 'inline_equation'
    EQUATION = 'equation'
    HYPERLINK = 'hyperlink'
    SEAL = 'seal'


class _ContentTypeV2:
    CODE = 'code'
    ALGORITHM = 'algorithm'
    EQUATION_INTERLINE = 'equation_interline'
    IMAGE = 'image'
    SEAL = 'seal'
    TABLE = 'table'
    CHART = 'chart'
    TABLE_SIMPLE = 'simple_table'
    TABLE_COMPLEX = 'complex_table'
    LIST = 'list'
    LIST_TEXT = 'text_list'
    LIST_REF = 'reference_list'
    INDEX = 'index'
    TITLE = 'title'
    PARAGRAPH = 'paragraph'
    SPAN_TEXT = 'text'
    SPAN_EQUATION_INLINE = 'equation_inline'
    SPAN_PHONETIC = 'phonetic'
    SPAN_MD = 'md'
    SPAN_CODE_INLINE = 'code_inline'
    PAGE_HEADER = 'page_header'
    PAGE_FOOTER = 'page_footer'
    PAGE_NUMBER = 'page_number'
    PAGE_ASIDE_TEXT = 'page_aside_text'
    PAGE_FOOTNOTE = 'page_footnote'


class _MakeMode:
    MM_MD = 'mm_markdown'
    NLP_MD = 'nlp_markdown'
    CONTENT_LIST = 'content_list'
    CONTENT_LIST_V2 = 'content_list_v2'
