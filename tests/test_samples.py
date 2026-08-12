"""Every sample is a diagram a model really drew, kept as a regression corpus.

The contract is narrow on purpose: after repair, no box may be missing, and the
box and edge counts must still match. That is enough to catch a tolerance change
in `asciigrid` that quietly drops content from somebody's picture.
"""

import pytest

from src.asciigrid import audit, parse, repair
from src.capture import SAMPLES, orphans

SAMPLE_FILES = sorted(SAMPLES.glob("*.txt"))


def load(path):
    lines = path.read_text().split("\n")
    meta = {}
    while lines and lines[0].startswith("#"):
        meta.update(part.split("=", 1) for part in lines.pop(0).lstrip("# ").split() if "=" in part)
    return "\n".join(lines), {k: int(v) for k, v in meta.items()}


def test_the_corpus_is_not_empty():
    assert SAMPLE_FILES, "no samples: see docs/adding-a-pattern.md"


@pytest.mark.parametrize("path", SAMPLE_FILES, ids=lambda p: p.stem)
def test_sample_survives_repair(path):
    """No box may go missing, and the counts are pinned: read more of a drawing
    and the test fails until you update the header on purpose."""
    art, expected = load(path)
    fixed = repair(art)
    diagram = parse(fixed)

    assert audit(fixed, fatal_only=True) == [], "a box would be missing from the picture"
    assert len(diagram.nodes) == expected["boxes"]
    assert len(diagram.edges) == expected["edges"]
    assert len(orphans(diagram)) == expected["orphans"], orphans(diagram)
