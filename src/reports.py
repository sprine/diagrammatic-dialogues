"""The inbox for renders that came out wrong.

A bad render is a training example, not a patch. Nothing here edits the
rendering pipeline — `asciigrid` and `sketch` are the model's output surface,
and quietly correcting them by hand hides the very failures the next round of
training needs to see.

Each report is two files sharing a stem: the record, and the wrong picture
beside it so a reviewer can look at what was actually produced.

    training-data/2026-08-12T204133Z-preload-bridge-detail.json
    training-data/2026-08-12T204133Z-preload-bridge-detail.svg
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from .asciigrid import audit, parse, repair
from .sketch import render

INBOX = Path(__file__).resolve().parent.parent / "training-data"


def _slug(text: str) -> str:
    return re.sub(r"-+", "-", re.sub(r"[^a-z0-9]+", "-", (text or "untitled").lower())).strip("-")[:48]


def _diagnosis(art: str) -> dict:
    """What the current pipeline made of the drawing — the half a reviewer
    cannot reconstruct later, once the code has moved on."""
    d = parse(art)
    linked = {e.source for e in d.edges} | {e.target for e in d.edges}
    return {
        "boxes": [n.label.replace("\n", " ") for n in d.nodes],
        "edges": [
            {"from": d.node(e.source).label.replace("\n", " "),
             "to": d.node(e.target).label.replace("\n", " "),
             "label": e.label}
            for e in d.edges
        ],
        "loose_text": [n.text for n in d.notes],
        "unconnected_boxes": [
            n.label.replace("\n", " ") for n in d.nodes if n.id not in linked and not n.parent
        ],
        "audit": audit(art),
    }


def write(card: dict, description: str) -> Path:
    """File one report. Returns the path of the record."""
    INBOX.mkdir(parents=True, exist_ok=True)
    raw = card.get("ascii") or ""
    art = repair(raw)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H%M%SZ")
    stem = INBOX / f"{stamp}-{_slug(card.get('title'))}"

    stem.with_suffix(".svg").write_text(
        render(parse(art), seed=card.get("id", "report"), width=1200, height=900,
               ratio=0.95, standalone=True)
    )
    record = {
        "reported_at": stamp,
        "description": description,
        "ascii": art,
        "ascii_as_drawn": raw if raw != art else None,  # before repair straightened it
        "produced": _diagnosis(art),
        "svg": stem.with_suffix(".svg").name,
        "card": {
            "id": card.get("id"),
            "title": card.get("title"),
            "model": card.get("model"),
            "effort": card.get("effort"),
            "remark": card.get("remark"),
        },
    }
    path = stem.with_suffix(".json")
    path.write_text(json.dumps(record, indent=2) + "\n")
    return path
