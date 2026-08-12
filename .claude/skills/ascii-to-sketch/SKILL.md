---
name: ascii-to-sketch
description: Turn an ASCII box diagram into a hand-drawn SVG sketch. Use when asked to draw a diagram of code or a system, to render an ASCII diagram as a picture, to produce a sketch/excalidraw-style SVG, or when writing a prompt that asks a model for a diagram this repo will render. Covers the ASCII grammar the parser accepts and the failure modes that silently drop boxes.
---

Draw in ASCII first, then render. ASCII is not a stepping stone to a "real"
diagram format — it is the constraint that keeps the diagram simple. A grid 78
columns wide holds about nine boxes, so whoever draws it has to decide what
matters instead of transcribing everything.

## The pipeline

```
ascii  ->  repair()  ->  parse()  ->  render()  ->  svg
           straighten   grid to      geometry to
           near-misses  geometry     hand-drawn
```

`src/asciigrid.py` owns the first two, `src/sketch.py` the last. The grid *is*
the layout — boxes land where they were drawn, so there is no layout engine and
the picture always matches the text it came from.

```bash
printf '+------+   +------+\n| a    |-->| b    |\n+------+   +------+\n' \
  | uv run python -m src.sketch > diagram.svg
```

In Python:

```python
from src.asciigrid import parse, repair, audit
from src.sketch import render

art = repair(raw_ascii)          # close boxes that missed by a column
svg = render(parse(art), seed="anything-stable", width=960, height=540)
complaints = audit(art)          # what repair could not fix; empty is good
```

`seed` fixes the wobble. Pass something stable (a card id, the diagram text) so
redrawing gives the identical picture — a diagram that shivers between renders
is one you cannot trust to be the same diagram.

## The grammar

**Boxes.** Corners `+`, horizontals `-`, verticals `|`. A box is a perfect
rectangle: every `|` in exactly the same column as the `+` above and below it.
Boxes may nest to show grouping; the outer box's label goes on its first inner
line.

**Connectors.** Horizontal and vertical runs, corners turned with `+`. `_` also
reads as a horizontal run. `\` and `/` are allowed for fanning several boxes
into one, each continuing along its own slope. Arrowheads `> < ^ v` must sit
directly against the border they point into — `--->|`, never `---> |`.

**Labels on connectors.** One short phrase, either inline between dashes
(`---- writes --->`) or on its own line crossing a vertical run. Both are
stitched back into the connector. Any other loose text becomes a caption.

## What actually goes wrong

Models are good at deciding what belongs in a diagram and bad at counting
characters. Nearly every failure is one of these:

- **A label wider than its border.** The box does not close, and it does not
  render as a wonky box — it vanishes entirely, silently. `repair()` widens the
  box and shifts the rest of the grid to keep columns aligned.
- **A row that drifted a column.** Same outcome. `repair()` snaps the body rows
  to the border columns.
- **Text sitting on a connector.** Splits one connector into two dangling stubs.
  Handled for the two label positions above; anything else is lost.

When asking a model for ASCII, spend the words on the alignment rule — it is the
one that breaks pictures. Tell it to pick each box's width from its longest
label and *then* draw the border, rather than drawing the border and hoping the
words fit. `src/prompts.py:DRAWING_RULES` is the wording that works.

After rendering, run `audit()`. Anything it returns is a box the reader will
never see, which is worth a redraw rather than shipping a diagram with holes.

To teach the model a shape it does not currently draw — and to make the parser
read it back — follow `docs/adding-a-pattern.md`. Prove the parser first, then
add the example, then capture a real turn to see what the model does with it.
`tests/samples/` holds the drawings collected that way.
