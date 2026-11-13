"""文件 → 纯文本。Notebook 保留 Markdown 和代码单元，不执行其中的代码。"""

import json
from pathlib import Path

from docx import Document as WordDocument
from pypdf import PdfReader


def _decode_text(path: Path) -> str:
    raw = path.read_bytes()
    if (
        raw.startswith((b"\xff\xfe", b"\xfe\xff"))
        or raw.count(b"\x00") > len(raw) // 10
    ):
        return raw.decode("utf-16", errors="replace")
    return raw.decode("utf-8", errors="replace")


def _notebook_text(path: Path) -> str:
    notebook = json.loads(_decode_text(path))
    sections = []
    for number, cell in enumerate(notebook.get("cells", []), start=1):
        if cell.get("cell_type") not in {"markdown", "code"}:
            continue
        source = cell.get("source", "")
        text = "".join(source) if isinstance(source, list) else str(source)
        if not text.strip():
            continue
        kind = "代码单元" if cell.get("cell_type") == "code" else "笔记"
        sections.append(f"### {kind} {number}\n{text.strip()}")
    return "\n\n".join(sections)


def read_material(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md", ".py", ".json", ".csv"}:
        return _decode_text(path)
    if suffix == ".ipynb":
        return _notebook_text(path)
    if suffix == ".pdf":
        reader = PdfReader(path)
        return "\n\n".join(
            f"### 第 {index} 页\n{page.extract_text() or ''}"
            for index, page in enumerate(reader.pages, start=1)
        )
    if suffix == ".docx":
        document = WordDocument(path)
        paragraphs = [
            paragraph.text
            for paragraph in document.paragraphs
            if paragraph.text.strip()
        ]
        for table in document.tables:
            paragraphs.extend(
                " | ".join(cell.text for cell in row.cells) for row in table.rows
            )
        return "\n".join(paragraphs)
    raise ValueError(
        "暂不支持此文件类型。请上传 PDF、DOCX、Markdown、TXT、Python、JSON、CSV 或 notebook 文件。"
    )
