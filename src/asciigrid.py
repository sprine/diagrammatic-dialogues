"""ASCII box-drawing -> geometry.

The grid *is* the layout. There is no auto-layout engine: boxes land where the
model drew them, which keeps rendering deterministic and makes the ASCII (which
the user can read) the honest source of truth for the picture they are shown.

Coordinates are grid cells: x = column, y = row, origin top-left.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field

# Unicode box-drawing is folded onto ASCII so the parser only ever sees + - | .
_FOLD = str.maketrans(
    {
        **{c: "+" for c in "┌┐└┘┼├┤┬┴╔╗╚╝╠╣╦╩╬"},
        **{c: "-" for c in "─═━"},
        **{c: "|" for c in "│║┃"},
        "▶": ">", "◀": "<", "▲": "^", "▼": "v", "→": ">", "←": "<", "↑": "^", "↓": "v",
    }
)

_ARROW_INTO = {">": (1, 0), "<": (-1, 0), "v": (0, 1), "^": (0, -1)}
_WIRE = set("-_|+><^v\\/")  # `_` is a horizontal run: models reach for it on a fan-in bus
_STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))
# A diagonal only ever links along its own slope, so two connectors that happen
# to pass corner-to-corner never get fused into one.
_SLOPE = {"\\": ((1, 1), (-1, -1)), "/": ((1, -1), (-1, 1))}

# dashes, inline text, dashes -> the text is an edge label bridging one wire.
_INLINE_LABEL = re.compile(r"(?<=-)( {0,2}[A-Za-z0-9][A-Za-z0-9 _.:/%]{0,28}[A-Za-z0-9] {0,2})(?=-)")


@dataclass
class Node:
    id: str
    label: str
    x: int
    y: int
    w: int
    h: int
    parent: str | None = None  # enclosing group, if any

    def cells(self):
        return (self.x, self.y, self.x + self.w - 1, self.y + self.h - 1)


@dataclass
class Edge:
    id: str
    source: str
    target: str
    points: list[tuple[int, int]]
    label: str = ""
    bidirectional: bool = False


@dataclass
class Note:
    id: str
    text: str
    x: int
    y: int
    w: int
    h: int


@dataclass
class Diagram:
    cols: int
    rows: int
    nodes: list[Node] = field(default_factory=list)
    edges: list[Edge] = field(default_factory=list)
    notes: list[Note] = field(default_factory=list)

    def node(self, nid: str) -> Node | None:
        return next((n for n in self.nodes if n.id == nid), None)

    def neighbours(self, nid: str) -> list[str]:
        """Labels one hop away — the 'what's around here?' the sketch asks for."""
        out = []
        for e in self.edges:
            other = e.target if e.source == nid else e.source if e.target == nid else None
            if other and (n := self.node(other)):
                out.append(n.label)
        me = self.node(nid)
        if me and me.parent and (p := self.node(me.parent)):
            out.append(f"inside {p.label}")
        return out


def parse(ascii_art: str) -> Diagram:
    grid = _grid(ascii_art)
    rows, cols = len(grid), len(grid[0]) if grid else 0
    if not rows or not cols:
        return Diagram(cols=0, rows=0)

    nodes = _find_boxes(grid, rows, cols)
    border, inside = _masks(nodes, rows, cols)
    _label_boxes(grid, nodes, inside)
    _nest(nodes)

    wire, inline_labels = _wire_mask(grid, border, inside, rows, cols)
    edges = _find_edges(grid, wire, border, nodes, rows, cols, inline_labels)
    notes = _find_notes(grid, border, inside, wire, rows, cols)
    notes = _attach_labels(notes, edges)

    return Diagram(cols=cols, rows=rows, nodes=nodes, edges=edges, notes=notes)


MAX_COLS, MAX_ROWS = 78, 26        # what the prompt asks for
AUDIT_COLS, AUDIT_ROWS = 88, 32    # what is worth a redraw; small overshoots render fine


def repair(ascii_art: str, rounds: int = 8) -> str:
    """Close boxes the model drew a column or two wrong.

    Models are good at deciding what goes in a diagram and bad at counting
    characters, and a box that misses by one does not render as a wonky box — it
    vanishes. The intent is never ambiguous, so rather than spend another CLI
    turn asking, snap the box to its own borders and widen it if the label
    overran. Only boxes that failed to parse are touched.
    """
    for _ in range(rounds):
        grid = _grid(ascii_art)
        if not grid:
            return ascii_art
        broken = _first_broken_box(grid, {(n.x, n.y, n.w, n.h) for n in parse(ascii_art).nodes})
        if not broken:
            break
        ascii_art = "\n".join("".join(row).rstrip() for row in _rebuild(grid, *broken))
    return ascii_art


def _h_runs(row: list[str]) -> list[tuple[int, int]]:
    """Every `+` pair joined only by dashes. Spans junctions too: a border like
    `+-------+--------+` is one box edge with a connector leaving the middle."""
    out = []
    for x, ch in enumerate(row):
        if ch != "+":
            continue
        for x2 in range(x + 1, len(row)):
            if row[x2] == "+":
                out.append((x, x2))
            elif row[x2] != "-":
                break
    return out


def _first_broken_box(grid, closed):
    """A top border, a bottom border roughly under it, and only text between."""
    rows = len(grid)
    for y in range(rows):
        for x1, x2 in _h_runs(grid[y]):
            for y2 in range(y + 2, min(y + 9, rows)):
                match = next(
                    ((b1, b2) for b1, b2 in _h_runs(grid[y2]) if abs(b1 - x1) <= 2 and abs(b2 - x2) <= 2),
                    None,
                )
                if not match:
                    continue
                body = grid[y + 1 : y2]
                span = slice(min(x1, match[0]), max(x2, match[1]) + 1)
                if any(_h_runs(row[span]) for row in body):
                    break  # encloses another box; a bare `+` in a label is fine
                if not all("|" in row[max(0, x1 - 2) : x2 + 3] for row in body):
                    break
                if (x1, y, x2 - x1 + 1, y2 - y + 1) in closed:
                    break  # already a real box
                return y, y2, x1, x2, match
    return None


def _rebuild(grid, y, y2, x1, x2, bottom):
    left = min(x1, bottom[0])
    right = max(x2, bottom[1])

    texts, overflow = [], right
    for row in grid[y + 1 : y2]:
        pipes = [i for i, c in enumerate(row) if c == "|"]
        # the pipe nearest each edge, so a neighbouring box is never mistaken for ours
        start = min(pipes, key=lambda i: abs(i - left), default=None)
        end = min(pipes, key=lambda i: abs(i - right), default=None)
        if start is None or end is None or end <= start or abs(start - left) > 3:
            texts.append("")
            continue
        texts.append("".join(row[start + 1 : end]).strip())
        overflow = max(overflow, end)

    # Erase the old box first: the characters that overran are why we are here.
    for row in grid[y + 1 : y2]:
        row[left : overflow + 1] = [" "] * (overflow + 1 - left)
    for row in (grid[y], grid[y2]):
        row[left : right + 1] = [" "] * (right + 1 - left)

    need = max((len(t) for t in texts), default=0) + 2
    if need > right - left + 1:
        grid = _insert_cols(grid, right, need - (right - left + 1))
        right = left + need - 1

    inner = right - left - 1
    for row in (grid[y], grid[y2]):
        row[left : right + 1] = ["+"] + ["-"] * inner + ["+"]
    for row, text in zip(grid[y + 1 : y2], texts):
        row[left : right + 1] = ["|"] + list(text.center(inner)) + ["|"]
    return grid


def _insert_cols(grid, at, n):
    """Widen the whole grid so every column below stays lined up.

    On rows carrying nothing but prose the split is nudged to a word boundary —
    the shift is the same either way, and `GalleryStore` should not come back as
    `Galler yStore`.
    """
    for row in grid:
        left = row[at - 1] if at else " "
        here = row[at] if at < len(row) else " "
        if left == "-" and here in "-+>":
            row[at:at] = ["-"] * n
            continue
        cut = at
        while cut > 0 and row[cut - 1] != " " and row[cut - 1] not in _WIRE:
            cut -= 1
        if cut == 0 or row[cut - 1] != " ":
            cut = at  # no clean break nearby; take the shift where it falls
        row[cut:cut] = [" "] * n
    return grid


def audit(ascii_art: str, diagram: Diagram | None = None, fatal_only: bool = False) -> list[str]:
    """Complaints about a drawing, phrased so they can be handed back to Claude.

    A box whose label overruns its border never closes, so it silently drops out
    of the picture. The symptom is unmistakable: leftover `|` marooned in the
    text layer. That one is fatal — content the reader will never see — and is
    worth a whole extra turn to fix. An oversized drawing merely renders large,
    so it is reported but not worth the latency of a redraw.
    """
    diagram = diagram or parse(ascii_art)
    grid = _grid(ascii_art)
    problems = []

    # Two independent symptoms of a box that never closed: a `|` marooned in the
    # text layer, and a border pair the box finder could not join up. Either one
    # means content the reader will never see.
    stray = {n.y for n in diagram.notes if "|" in n.text}
    unclosed = _first_broken_box(grid, {(n.x, n.y, n.w, n.h) for n in diagram.nodes})
    if unclosed:
        stray.add(unclosed[0])
    for y in sorted(stray)[:4]:
        line = "".join(grid[y]).rstrip() if y < len(grid) else ""
        problems.append(f"line {y + 1} has a box that never closes: {line!r}")
    if len(stray) > 4:
        problems.append(f"...and {len(stray) - 4} more lines like it")
    if not diagram.nodes and ascii_art.strip():
        problems.append("no box could be read at all")
    if fatal_only:
        return problems

    if grid and len(grid[0]) > AUDIT_COLS:
        problems.append(f"the drawing is {len(grid[0])} columns wide; the limit is {MAX_COLS}")
    if len(grid) > AUDIT_ROWS:
        problems.append(f"the drawing is {len(grid)} rows tall; the limit is {MAX_ROWS}")
    if len(diagram.nodes) > 12:
        problems.append(f"{len(diagram.nodes)} boxes is too many to read; the limit is 9")
    return problems


def _grid(text: str) -> list[list[str]]:
    lines = text.translate(_FOLD).replace("\t", "    ").split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    while lines and not lines[0].strip():
        lines.pop(0)
    width = max((len(l) for l in lines), default=0)
    return [list(l.ljust(width)) for l in lines]


def _find_boxes(grid, rows, cols) -> list[Node]:
    """Smallest valid rectangle per top-left corner. Distinct corners means an
    enclosing group and its children are both found."""
    found, seen = [], set()
    for y in range(rows):
        for x in range(cols):
            if grid[y][x] != "+":
                continue
            rect = _smallest_rect(grid, rows, cols, x, y)
            if rect and rect not in seen:
                seen.add(rect)
                found.append(rect)
    found.sort(key=lambda r: (r[1], r[0]))
    return [
        Node(id=f"n{i}", label="", x=x, y=y, w=x2 - x + 1, h=y2 - y + 1)
        for i, (x, y, x2, y2) in enumerate(found)
    ]


def _smallest_rect(grid, rows, cols, x, y):
    rights = []
    for x2 in range(x + 1, cols):
        c = grid[y][x2]
        if c == "+":
            rights.append(x2)
        elif c != "-":
            break
    if not rights:
        return None
    bottoms = []
    for y2 in range(y + 1, rows):
        c = grid[y2][x]
        if c == "+":
            bottoms.append(y2)
        elif c != "|":
            break
    for y2 in bottoms:
        for x2 in rights:
            if _closed(grid, x, y, x2, y2):
                return (x, y, x2, y2)
    return None


def _closed(grid, x, y, x2, y2) -> bool:
    if grid[y2][x2] != "+" or grid[y2][x] != "+" or grid[y][x2] != "+":
        return False
    if any(grid[y2][i] not in "-+" for i in range(x, x2 + 1)):
        return False
    if any(grid[j][x2] not in "|+" for j in range(y, y2 + 1)):
        return False
    return True


def _masks(nodes, rows, cols):
    border = [[False] * cols for _ in range(rows)]
    inside = [[None] * cols for _ in range(rows)]
    area = {n.id: n.w * n.h for n in nodes}
    for n in nodes:
        x, y, x2, y2 = n.cells()
        for i in range(x, x2 + 1):
            border[y][i] = border[y2][i] = True
        for j in range(y, y2 + 1):
            border[j][x] = border[j][x2] = True
        for j in range(y + 1, y2):
            for i in range(x + 1, x2):
                prev = inside[j][i]
                # innermost box wins, so nested groups label correctly
                if prev is None or area[n.id] < area[prev]:
                    inside[j][i] = n.id
    return border, inside


def _label_boxes(grid, nodes, inside):
    buckets: dict[str, list[str]] = {n.id: [] for n in nodes}
    for j, row in enumerate(inside):
        per_node: dict[str, list[str]] = {}
        for i, owner in enumerate(row):
            if owner:
                per_node.setdefault(owner, []).append(grid[j][i])
        for nid, chars in per_node.items():
            # a doubled border leaves a pipe stranded inside the label
            if (line := "".join(chars).strip().strip("|").strip()):
                buckets[nid].append(line)
    for n in nodes:
        n.label = "\n".join(buckets[n.id]).strip()


def _nest(nodes):
    for n in nodes:
        x, y, x2, y2 = n.cells()
        best = None
        for m in nodes:
            if m.id == n.id:
                continue
            mx, my, mx2, my2 = m.cells()
            if mx <= x and my <= y and mx2 >= x2 and my2 >= y2:
                if best is None or m.w * m.h < best.w * best.h:
                    best = m
        n.parent = best.id if best else None


_NEEDS = {  # a line character is only wire if it continues a structure
    "-": ((-1, 0), (1, 0)),
    "_": ((-1, 0), (1, 0)),
    ">": ((-1, 0), (1, 0)),
    "<": ((-1, 0), (1, 0)),
    "|": ((0, -1), (0, 1)),
    "^": ((0, -1), (0, 1)),
    "v": ((0, -1), (0, 1)),
    "+": ((-1, 0), (1, 0), (0, -1), (0, 1)),
    "\\": ((1, 1), (-1, -1), (0, -1), (0, 1)),
    "/": ((1, -1), (-1, 1), (0, -1), (0, 1)),
}


def _wire_mask(grid, border, inside, rows, cols):
    """Wire cells are line characters outside every box that actually connect to
    something. The check matters: the `v` in "views" is not an arrowhead, and the
    hyphen in "read-only" is not a connector.

    Inline edge labels are bridged afterwards so `--auth-->` stays one connector
    instead of splitting into two dangling stubs."""
    free = [
        [grid[y][x] in _WIRE and not border[y][x] and not inside[y][x] for x in range(cols)]
        for y in range(rows)
    ]
    wire = [[False] * cols for _ in range(rows)]
    for y in range(rows):
        for x in range(cols):
            if not free[y][x]:
                continue
            for dx, dy in _NEEDS[grid[y][x]]:
                nx, ny = x + dx, y + dy
                if 0 <= nx < cols and 0 <= ny < rows and (free[ny][nx] or border[ny][nx]):
                    wire[y][x] = True
                    break

    labels: dict[tuple[int, int], str] = {}
    for y in range(rows):
        line = "".join(grid[y])
        for m in _INLINE_LABEL.finditer(line):
            s, e = m.span()
            if any(border[y][i] or inside[y][i] for i in range(s, e)):
                continue
            if not (s and wire[y][s - 1]) or not (e < cols and wire[y][e]):
                continue  # only bridge text that sits between two live wires
            for i in range(s, e):
                wire[y][i] = True
            labels[(s, y)] = m.group(1).strip()

    # Same idea vertically: a caption written across a descending connector. It
    # may wrap over several lines, and need not cover the connector's own column.
    for y in range(1, rows - 1):
        for x in range(cols):
            # Only hang off a vertical run: text under a horizontal arrow is that
            # arrow's own label, not a caption on some connector passing through.
            if not wire[y - 1][x] or grid[y - 1][x] not in "|+v^\\/":
                continue
            if wire[y][x] or border[y][x] or inside[y][x]:
                continue
            spans, caption = [], []
            for y2 in range(y, min(y + _CAPTION_ROWS + 1, rows)):
                if wire[y2][x] or border[y2][x] or free[y2][x]:
                    # An arrowhead stranded between two captions was pruned as
                    # isolated; the caption is the evidence that it belongs.
                    wire[y2][x] = wire[y2][x] or free[y2][x]
                    break
                span = _caption_span(grid, border, inside, y2, x, cols)
                if span is None:
                    spans = []
                    break
                spans.append((y2, span))
            else:
                spans = []  # ran out of rows without meeting the wire again
            if not spans or sum(e - s for _, (s, e) in spans) > 60:
                continue
            for y2, (s, e) in spans:
                for i in range(s, e + 1):
                    wire[y2][i] = True
                wire[y2][x] = True  # the connector passes behind the words
                caption.append("".join(grid[y2][s : e + 1]).strip(" |+v^<>-_\\/"))
            labels[(x, spans[0][0])] = " ".join(w for w in caption if w)
    return wire, labels


_CAPTION_ROWS = 3
_CAPTION_REACH = 12


def _caption_span(grid, border, inside, y, x, cols):
    """The phrase on this row nearest column x, or None if the row is bare."""
    if border[y][x] or inside[y][x]:
        return None
    at = next(
        (
            i
            for d in range(_CAPTION_REACH)
            for i in (x - d, x + d)
            if 0 <= i < cols and grid[y][i].strip() and not border[y][i] and not inside[y][i]
        ),
        None,
    )
    return None if at is None else _text_span(grid, border, inside, y, at, cols)


def _text_span(grid, border, inside, y, x, cols):
    """The whole phrase around a cell, hopping single spaces between words."""
    free = lambda i: 0 <= i < cols and not border[y][i] and not inside[y][i]
    s = x
    while free(s - 1) and (grid[y][s - 1] != " " or (free(s - 2) and grid[y][s - 2].strip())):
        s -= 1
    e = x
    while free(e + 1) and (grid[y][e + 1] != " " or (free(e + 2) and grid[y][e + 2].strip())):
        e += 1
    return s, e


def _linked(grid, x, y, rows, cols):
    """Orthogonal neighbours always; diagonal ones only along a slope character."""
    for dx, dy in _STEPS:
        if 0 <= x + dx < cols and 0 <= y + dy < rows:
            yield x + dx, y + dy
    for (dx, dy) in ((1, 1), (-1, -1), (1, -1), (-1, 1)):
        nx, ny = x + dx, y + dy
        if not (0 <= nx < cols and 0 <= ny < rows):
            continue
        if (dx, dy) in _SLOPE.get(grid[y][x], ()) or (-dx, -dy) in _SLOPE.get(grid[ny][nx], ()):
            yield nx, ny


def _components(grid, wire, rows, cols):
    seen = [[False] * cols for _ in range(rows)]
    for y in range(rows):
        for x in range(cols):
            if not wire[y][x] or seen[y][x]:
                continue
            comp, q = [], deque([(x, y)])
            seen[y][x] = True
            while q:
                cx, cy = q.popleft()
                comp.append((cx, cy))
                for nx, ny in _linked(grid, cx, cy, rows, cols):
                    if wire[ny][nx] and not seen[ny][nx]:
                        seen[ny][nx] = True
                        q.append((nx, ny))
            yield comp


def _find_edges(grid, wire, border, nodes, rows, cols, inline_labels) -> list[Edge]:
    owner = {}
    for n in nodes:
        x, y, x2, y2 = n.cells()
        for i in range(x, x2 + 1):
            owner[(i, y)] = owner[(i, y2)] = n.id
        for j in range(y, y2 + 1):
            owner[(x, j)] = owner[(x2, j)] = n.id

    edges: list[Edge] = []
    for comp in _components(grid, wire, rows, cols):
        cells = set(comp)
        # attachments: (node_id) -> (wire cell, border cell, arrow points into node)
        attach: dict[str, tuple[tuple[int, int], tuple[int, int], bool]] = {}
        for cx, cy in comp:
            ch = grid[cy][cx]
            for dx, dy in _STEPS:
                nid = owner.get((cx + dx, cy + dy))
                if not nid:
                    continue
                into = _ARROW_INTO.get(ch) == (dx, dy)
                cur = attach.get(nid)
                if cur is None or (into and not cur[2]):
                    attach[nid] = ((cx, cy), (cx + dx, cy + dy), into)
        if len(attach) < 2:
            continue
        trace = lambda a, b: _trace(cells, grid, rows, cols, a, b)

        targets = [k for k, v in attach.items() if v[2]]
        sources = [k for k, v in attach.items() if not v[2]]
        bidir = False
        if not sources:  # `<--->` : everything is an arrowhead
            bidir = True
            sources, targets = targets[:1], targets[1:]
        elif not targets:
            sources, targets = sources[:1], sources[1:]

        label = next((t for (lx, ly), t in inline_labels.items() if (lx, ly) in cells), "")
        src = sources[0]
        for tgt in targets:
            path = trace(attach[src][0], attach[tgt][0])
            pts = [attach[src][1], *path, attach[tgt][1]]
            edges.append(
                Edge(
                    id=f"e{len(edges)}",
                    source=src,
                    target=tgt,
                    points=_simplify(pts),
                    label=label,
                    bidirectional=bidir,
                )
            )
        for extra in sources[1:]:
            path = trace(attach[extra][0], attach[targets[0]][0]) if targets else []
            if path:
                edges.append(
                    Edge(
                        id=f"e{len(edges)}",
                        source=extra,
                        target=targets[0],
                        points=_simplify([attach[extra][1], *path, attach[targets[0]][1]]),
                        label=label,
                    )
                )
    return edges


def _trace(cells, grid, rows, cols, start, goal) -> list[tuple[int, int]]:
    prev = {start: None}
    q = deque([start])
    while q:
        cur = q.popleft()
        if cur == goal:
            break
        for nxt in _linked(grid, cur[0], cur[1], rows, cols):
            if nxt in cells and nxt not in prev:
                prev[nxt] = cur
                q.append(nxt)
    if goal not in prev:
        return [start, goal]
    path, cur = [], goal
    while cur is not None:
        path.append(cur)
        cur = prev[cur]
    return path[::-1]


def _simplify(points):
    out = []
    for p in points:
        if out and out[-1] == p:
            continue
        out.append(p)
    if len(out) < 3:
        return out
    kept = [out[0]]
    for prev, cur, nxt in zip(out, out[1:], out[2:]):
        if (cur[0] - prev[0]) * (nxt[1] - cur[1]) != (cur[1] - prev[1]) * (nxt[0] - cur[0]):
            kept.append(cur)
    kept.append(out[-1])
    return kept


def _find_notes(grid, border, inside, wire, rows, cols) -> list[Note]:
    """Free text outside boxes. Runs on adjacent rows that overlap in columns
    merge into one block so a wrapped caption stays one note."""
    runs = []
    for y in range(rows):
        x = 0
        while x < cols:
            if grid[y][x] == " " or border[y][x] or inside[y][x] or wire[y][x]:
                x += 1
                continue
            s = x
            gap = 0
            text = []
            while x < cols:
                c = grid[y][x]
                free = not (border[y][x] or inside[y][x] or wire[y][x])
                if c == " " and free:
                    gap += 1
                    if gap > 1:
                        break
                elif free:
                    gap = 0
                else:
                    break
                text.append(c)
                x += 1
            body = "".join(text).rstrip()
            if body.strip():
                runs.append({"y": y, "x": s, "w": len(body), "text": body})
            x += 1

    blocks: list[dict] = []
    for r in runs:
        for b in blocks:
            if b["y"] + b["h"] == r["y"] and r["x"] < b["x"] + b["w"] and b["x"] < r["x"] + r["w"]:
                b["lines"].append(r["text"])
                b["h"] += 1
                b["w"] = max(b["w"], r["x"] + r["w"] - min(b["x"], r["x"]))
                b["x"] = min(b["x"], r["x"])
                break
        else:
            blocks.append({"x": r["x"], "y": r["y"], "w": r["w"], "h": 1, "lines": [r["text"]]})

    return [
        Note(id=f"t{i}", text="\n".join(b["lines"]).strip(), x=b["x"], y=b["y"], w=b["w"], h=b["h"])
        for i, b in enumerate(blocks)
    ]


def _attach_labels(notes, edges):
    """A note hugging an unlabelled connector is that connector's label."""
    keep = []
    for note in notes:
        if "\n" in note.text or len(note.text) > 24:
            keep.append(note)
            continue
        host = None
        for e in edges:
            if e.label:
                continue
            for px, py in e.points:
                if abs(py - note.y) <= 1 and note.x - 2 <= px <= note.x + note.w + 1:
                    host = e
                    break
            if host:
                break
        if host:
            host.label = note.text
        else:
            keep.append(note)
    return keep
