"""Geometry -> hand-drawn SVG.

The wobble is seeded per diagram, so a picture looks identical every time it is
drawn. A diagram that shivers when you scroll past it is a diagram you cannot
trust to be the same diagram.

Runs standalone:  python -m src.sketch < diagram.txt > diagram.svg
"""

from __future__ import annotations

import math
import sys
from html import escape

from .asciigrid import Diagram, parse, repair

ROUGH = 1.15   # architect sloppiness
ASPECT = 1.8   # cell height / cell width
PAD = 1.4      # cells of margin around the drawing
MASK = 0xFFFFFFFF


def _hash(text: str) -> int:
    h = 2166136261
    for ch in text:
        h = ((h ^ ord(ch)) * 16777619) & MASK
    return h


class _Rng:
    """mulberry32 — small, fast, and identical across runs."""

    def __init__(self, seed: int):
        self.a = seed & MASK

    def __call__(self) -> float:
        self.a = (self.a + 0x6D2B79F5) & MASK
        t = (self.a ^ (self.a >> 15)) * (1 | self.a) & MASK
        t = (t + ((t ^ (t >> 7)) * (61 | t) & MASK)) & MASK ^ t
        return ((t ^ (t >> 14)) & MASK) / 4294967296


def _n(value: float) -> str:
    return f"{value:.2f}".rstrip("0").rstrip(".")


def _stroke(x1, y1, x2, y2, rng, amp) -> str:
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5 or 1
    wob = min(amp * (0.5 + length / 90), amp * 2.6)
    j = lambda: (rng() * 2 - 1) * wob
    mx = (x1 + x2) / 2 + (-dy / length) * j() * 1.2
    my = (y1 + y2) / 2 + (dx / length) * j() * 1.2
    return (
        f"M{_n(x1 + j())} {_n(y1 + j())} Q{_n(mx)} {_n(my)} "
        f"{_n(x2 + j())} {_n(y2 + j())}"
    )


def _line(out, x1, y1, x2, y2, rng, amp, cls="ink"):
    """Two passes is what makes a line read as drawn rather than computed."""
    out.append(f'<path class="{cls}" d="{_stroke(x1, y1, x2, y2, rng, amp)}"/>')
    out.append(f'<path class="{cls}" d="{_stroke(x1, y1, x2, y2, rng, amp * 0.7)}"/>')


def _wobbly_rect(x, y, w, h, rng, amp) -> str:
    j = lambda: (rng() * 2 - 1) * amp
    return (
        f"M{_n(x + j())} {_n(y + j())} L{_n(x + w + j())} {_n(y + j())} "
        f"L{_n(x + w + j())} {_n(y + h + j())} L{_n(x + j())} {_n(y + h + j())} Z"
    )


def render(
    diagram: Diagram,
    *,
    seed: str = "x",
    width: float = 960,
    height: float = 540,
    ratio: float = 0.6,
    flags: list[dict] | None = None,
    interactive: bool = False,
    standalone: bool = False,
) -> str:
    flags = flags or []
    cols = max(diagram.cols, 1)
    rows = max(diagram.rows, 1)

    # Sketch note: size from a ratio of the container, never from zoom. Height is
    # bounded too, or a tall diagram runs off the bottom of its card.
    cell = max(3.0, min(16.0, (width * ratio) / (cols + PAD * 2), height / ((rows + PAD * 2) * ASPECT)))
    ch = cell * ASPECT
    total_w = (cols + PAD * 2) * cell
    total_h = (rows + PAD * 2) * ch
    ox, oy = PAD * cell, PAD * ch
    px = lambda c: ox + c * cell
    py = lambda r: oy + r * ch
    amp = ROUGH * (cell / 9)

    groups = {n.parent for n in diagram.nodes if n.parent}
    by_id = {n.id: n for n in diagram.nodes}
    fills, boxes, texts, wires, marks = [], [], [], [], []

    # Largest first, so a child sits on top of its container's fill.
    for node in sorted(diagram.nodes, key=lambda n: -n.w * n.h):
        rng = _Rng(_hash(seed + node.id))
        x, y, w, h = px(node.x), py(node.y), node.w * cell, node.h * ch
        is_group = node.id in groups
        fills.append(
            f'<path class="{"fill-group" if is_group else "fill-box"}" '
            f'd="{_wobbly_rect(x, y, w, h, rng, amp * 0.6)}"/>'
        )
        boxes.append(f'<g class="box{" group" if is_group else ""}" data-node="{node.id}">')
        _line(boxes, x, y, x + w, y, rng, amp)
        _line(boxes, x + w, y, x + w, y + h, rng, amp)
        _line(boxes, x + w, y + h, x, y + h, rng, amp)
        _line(boxes, x, y + h, x, y, rng, amp)
        boxes.append("</g>")

        lines = [ln for ln in (node.label or "").split("\n") if ln]
        longest = max((len(ln) for ln in lines), default=1)
        size = max(8.0, min(ch * 0.64, ((node.w - 1.2) * cell) / (longest * 0.6)))
        # A name centres well; a list of members does not.
        ranged = is_group or len(lines) > 3
        start = y + ch * 0.95 if is_group else y + h / 2 - (len(lines) - 1) * size * 1.25 / 2
        for i, text in enumerate(lines):
            anchor = "start" if ranged else "middle"
            tx = x + cell * 1.2 if ranged else x + w / 2
            texts.append(
                f'<text class="label" x="{_n(tx)}" y="{_n(start + i * size * 1.25)}" '
                f'font-size="{_n(size)}" text-anchor="{anchor}" '
                f'dominant-baseline="middle">{escape(text)}</text>'
            )

        if interactive:
            label = escape((node.label or "").replace("\n", " "), quote=True)
            marks.append(
                f'<rect class="hit" x="{_n(x)}" y="{_n(y)}" width="{_n(w)}" height="{_n(h)}" '
                f'data-node="{node.id}" data-label="{label}">'
                f"<title>Plant a flag here</title></rect>"
            )

    for edge in diagram.edges:
        rng = _Rng(_hash(seed + edge.id))
        pts = [(px(c) + cell / 2, py(r) + ch / 2) for c, r in edge.points]
        wires.append('<g class="wire">')
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            _line(wires, x1, y1, x2, y2, rng, amp * 0.85, "ink wire-ink")
        if len(pts) >= 2:
            _arrow(wires, pts[-2], pts[-1], rng, amp, cell)
            if edge.bidirectional:
                _arrow(wires, pts[1], pts[0], rng, amp, cell)
        wires.append("</g>")

        if edge.label:
            mx, my = pts[len(pts) // 2] if pts else (0, 0)
            size = max(7.0, ch * 0.44)
            box_w = len(edge.label) * size * 0.6 + size * 0.7
            texts.append(
                f'<rect class="label-bg" x="{_n(mx - box_w / 2)}" y="{_n(my - size * 0.75)}" '
                f'width="{_n(box_w)}" height="{_n(size * 1.5)}"/>'
            )
            texts.append(
                f'<text class="edge-label" x="{_n(mx)}" y="{_n(my)}" font-size="{_n(size)}" '
                f'text-anchor="middle" dominant-baseline="middle">{escape(edge.label)}</text>'
            )

    for note in diagram.notes:
        size = max(7.0, ch * 0.46)
        for i, text in enumerate(note.text.split("\n")):
            texts.append(
                f'<text class="note" x="{_n(px(note.x))}" '
                f'y="{_n(py(note.y) + ch * 0.7 + i * size * 1.3)}" '
                f'font-size="{_n(size)}">{escape(text)}</text>'
            )

    for flag in flags:
        node = by_id.get(flag.get("anchor_node"))
        anchor = (
            (px(node.x + node.w) - cell * 0.5, py(node.y) + ch * 0.4)
            if node
            else (px(cols) - cell, py(0) + ch * 0.4)
        )
        marks.append(_pennant(anchor, flag, cell, ch, _Rng(_hash(seed + "f" + flag["card_id"])), amp))

    body = "".join(fills + wires + boxes + texts + marks)
    style = f"<style>{STANDALONE_CSS}</style>" if standalone else ""
    ground = f'<rect width="100%" height="100%" fill="#f4f2ec"/>' if standalone else ""
    return (
        f'<svg class="sketch" xmlns="http://www.w3.org/2000/svg" '
        f'viewBox="0 0 {_n(total_w)} {_n(total_h)}" width="{_n(total_w)}" '
        f'height="{_n(total_h)}" shape-rendering="geometricPrecision">'
        f"{style}{ground}{body}</svg>"
    )


def _arrow(out, start, end, rng, amp, cell):
    angle = math.atan2(end[1] - start[1], end[0] - start[0])
    length = min(cell * 1.1, 11)
    for spread in (2.6, -2.6):
        _line(
            out, end[0], end[1],
            end[0] + math.cos(angle + spread) * length,
            end[1] + math.sin(angle + spread) * length,
            rng, amp * 0.5, "ink wire-ink",
        )


def _pennant(anchor, flag, cell, ch, rng, amp) -> str:
    """A flag in a field: a short pole out of the box, a numbered pennant on top."""
    x, y = anchor
    top = y - ch * 1.15
    w, h = cell * 2.2, ch * 0.86
    classes = "flag"
    if flag.get("on_path"):
        classes += " on-path"
    if flag.get("status") == "running":
        classes += " pending"
    out = [f'<g class="{classes}" data-card="{flag["card_id"]}">']
    out.append(f'<circle class="flag-foot" cx="{_n(x)}" cy="{_n(y)}" r="{_n(max(2.5, cell * 0.28))}"/>')
    _line(out, x, y, x, top, rng, amp * 0.5, "ink flag-pole")
    out.append(
        f'<path class="flag-cloth" d="M{_n(x)} {_n(top)} L{_n(x + w)} '
        f'{_n(top + h * 0.28)} L{_n(x)} {_n(top + h)} Z"/>'
    )
    out.append(
        f'<text class="flag-num" x="{_n(x + w * 0.34)}" y="{_n(top + h * 0.42)}" '
        f'font-size="{_n(max(7, ch * 0.42))}" text-anchor="middle" '
        f'dominant-baseline="middle">{flag["n"]}</text>'
    )
    tip = "{}. {}".format(flag["n"], flag.get("remark", "")[:70])
    out.append(f"<title>{escape(tip)}</title></g>")
    return "".join(out)


STANDALONE_CSS = (
    ".ink{fill:none;stroke:#17140f;stroke-width:1.5;stroke-linecap:round}"
    ".wire-ink{stroke-width:1.25}"
    ".fill-box{fill:#fff}.fill-group{fill:rgba(23,20,15,.035)}.label-bg{fill:#fffefb}"
    '.label,.edge-label,.note{font-family:ui-monospace,"SF Mono",Menlo,monospace}'
    ".label{fill:#17140f}.edge-label,.note{fill:#6b655c}"
)


def main():
    """ASCII on stdin, a standalone SVG on stdout."""
    art = sys.stdin.read()
    svg = render(
        parse(repair(art)), seed=art[:64] or "cli",
        width=1200, height=900, ratio=0.95, standalone=True,
    )
    sys.stdout.write(svg + "\n")


if __name__ == "__main__":
    main()
