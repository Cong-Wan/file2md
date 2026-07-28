'''
Author: wilbur
Version: 1.0
  Date: 2026-04-10
  Description: 入口文件：DocxToMarkdownConverter 公共 API
'''

import os
from pathlib import Path
from io import BytesIO

from .docx_converter import _DocxConverter
from .middle_json import _result_to_middle_json, _MemoryImageWriter
from .markdown_renderer import _union_make, _MakeMode


class DocxToMarkdownConverter:
    """Standalone converter: DOCX document → Markdown file.

    External deps: python-docx mammoth lxml pandas Pillow loguru pydantic pylatexenc beautifulsoup4
    """

    def convert_file(
        self,
        file_path: str,
        output_path: str | None = None,
        image_mode: str = 'file',
        image_dir: str | None = None,
        return_middle_json: bool = False,
    ) -> str | tuple[str, dict]:
        """Convert a .docx file to Markdown and write to disk.

        Args:
            file_path:          Path to the input .docx file.
            output_path:        Path for the output .md file.
                                Defaults to same directory as file_path, same stem, .md extension.
            image_mode:         'file'   — save images to image_dir, use relative path in markdown.
                                'base64' — embed images as base64 data URIs (image_dir ignored).
            image_dir:          Directory for extracted images when image_mode='file'.
                                Defaults to <output_path directory>/images/.
            return_middle_json: When True, return (markdown_str, middle_json_dict).

        Returns:
            Markdown string, or (markdown string, middle_json dict) if return_middle_json=True.
        """
        with open(file_path, 'rb') as fh:
            file_bytes = fh.read()

        if output_path is None:
            src = Path(file_path)
            output_path = str(src.parent / (src.stem + '.md'))

        return self._convert(
            file_bytes=file_bytes,
            output_path=output_path,
            image_mode=image_mode,
            image_dir=image_dir,
            return_middle_json=return_middle_json,
        )

    def convert_bytes(
        self,
        file_bytes: bytes,
        output_path: str,
        image_mode: str = 'file',
        image_dir: str | None = None,
        return_middle_json: bool = False,
    ) -> str | tuple[str, dict]:
        """Convert .docx bytes to Markdown and write to disk.

        Args:
            file_bytes:         Raw bytes of the .docx file.
            output_path:        Required. Path for the output .md file.
            image_mode:         Same as convert_file.
            image_dir:          Same as convert_file. Defaults to <output_path directory>/images/.
            return_middle_json: Same as convert_file.

        Returns:
            Markdown string, or (markdown string, middle_json dict) if return_middle_json=True.
        """
        return self._convert(
            file_bytes=file_bytes,
            output_path=output_path,
            image_mode=image_mode,
            image_dir=image_dir,
            return_middle_json=return_middle_json,
        )

    def _convert(
        self,
        file_bytes: bytes,
        output_path: str,
        image_mode: str,
        image_dir: str | None,
        return_middle_json: bool,
    ) -> str | tuple[str, dict]:
        output_path = str(output_path)
        output_dir = os.path.dirname(os.path.abspath(output_path))

        if image_mode not in ('file', 'base64'):
            raise ValueError(f"image_mode must be 'file' or 'base64', got {image_mode!r}")

        # Resolve image_dir
        if image_mode == 'file':
            if image_dir is None:
                image_dir = os.path.join(output_dir, 'images')
            os.makedirs(image_dir, exist_ok=True)

            class _FileImageWriter:
                def __init__(self, directory: str):
                    self._dir = directory

                def write(self, path: str, data: bytes) -> None:
                    dest = os.path.join(self._dir, path) if not os.path.isabs(path) else path
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(dest, 'wb') as fp:
                        fp.write(data)

            image_writer = _FileImageWriter(image_dir)
            img_bucket_path = os.path.relpath(image_dir, output_dir)
        else:
            image_writer = _MemoryImageWriter()
            img_bucket_path = ''

        # Step 1: parse DOCX
        converter = _DocxConverter()
        converter.convert(BytesIO(file_bytes))

        # Step 2: build middle_json
        middle_json = _result_to_middle_json(converter.pages, image_writer)

        # Step 3: render markdown
        markdown = _union_make(
            middle_json['pdf_info'],
            _MakeMode.MM_MD,
            img_bucket_path,
        )

        # Write output .md file
        os.makedirs(output_dir, exist_ok=True)
        with open(output_path, 'w', encoding='utf-8') as fp:
            fp.write(markdown)

        if return_middle_json:
            return markdown, middle_json
        return markdown
