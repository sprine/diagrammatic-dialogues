"""Gitignored cache of PDF -> markdown text, keyed by the PDF's resolved path.

Docs mode reads the same PDFs across many drill-downs, and across trails that
point at the same folder from different depths. This is what stops every one of
those turns from re-running the pdf-to-md skill on a file that has not changed.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIR = ROOT / "pdf-cache"


def path_for(pdf: Path) -> Path:
    """Cache path for a PDF. Stable across trails: derived from its resolved
    path, not from where a particular trail's target directory happens to be."""
    stem = str(pdf.resolve()).lstrip("/").replace("/", "__")
    return DIR / f"{stem}.md"
