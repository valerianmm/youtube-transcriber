"""Markdown to PDF via WeasyPrint (needs the pango system library)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import markdown as _markdown

_MESSAGE = "PDF export needs pango: run `brew install pango`"

_CSS = """
@page { size: A4; margin: 2cm; }
body { font-family: -apple-system, "Helvetica Neue", Helvetica, Arial, sans-serif;
       font-size: 11pt; line-height: 1.5; }
code, pre { font-family: Menlo, Monaco, "Courier New", monospace;
            background: #f2f2f2; }
code { padding: 0 0.2em; }
pre { padding: 0.6em; white-space: pre-wrap; }
pre code { padding: 0; }
table { border-collapse: collapse; }
th, td { border: 1px solid #ccc; padding: 0.3em 0.6em; }
"""


def _refuse_fetch(url, *args, **kwargs):
    raise ValueError(f"External resources are not allowed in exports: {url}")


class PdfUnavailable(Exception):
    pass


def markdown_to_pdf(md: str, out: Path) -> None:
    out = Path(out)
    body = _markdown.markdown(md, extensions=["tables", "fenced_code", "sane_lists"])
    html = (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<style>{_CSS}</style></head><body>{body}</body></html>"
    )
    try:
        from weasyprint import HTML
    except (ImportError, OSError) as e:
        raise PdfUnavailable(_MESSAGE) from e

    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=out.parent, prefix=f".{out.name}.", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        try:
            HTML(string=html, url_fetcher=_refuse_fetch).write_pdf(str(tmp))
        except OSError as e:
            if isinstance(e, (FileNotFoundError, PermissionError)) or e.errno:
                raise
            raise PdfUnavailable(_MESSAGE) from e
        os.replace(tmp, out)
    finally:
        tmp.unlink(missing_ok=True)
