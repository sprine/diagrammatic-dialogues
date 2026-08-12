# Speak to me in Diagrams

A local tool for reading a codebase you did not write. Claude draws it as a
diagram; you click a box to plant a flag and ask something; the answer arrives as
the next diagram, with the previous ones still on screen and every flag still
clickable. See `sketch_1.png` for the original design and `README.md` for the
user-facing description.

```bash
./run.sh                              # http://127.0.0.1:8420
uv run --extra dev python -m pytest -q
```

`SID_PORT`, `SID_DB`, `SID_OPEN=0` override port, database, and browser launch.

## Shape

```
src/asciigrid.py   ASCII grid -> geometry; also repair() and audit()
src/sketch.py      geometry -> hand-drawn SVG; also `python -m src.sketch` (stdin -> svg)
src/claude_cli.py  drives `claude -p`, streams progress, redraws a broken diagram
src/prompts.py     the drawing contract, the output schema, the depth ladder
src/models.py      SQLite; a trail is a rooted tree of cards
src/web.py         FastAPI + SSE; renders each card's SVG server-side
src/capture.py     dev tool: run one real turn, file the ascii as a parser sample
static/app.js      the strip, the composer, the live event stream
tests/samples/     real model drawings, pinned to golden counts
```

Teaching Claude a new shape — a fan-in, a decision branch, a swimlane — means
changing a worked example *and* the parser that reads it back, in that order.
See `docs/adding-a-pattern.md`.

One card is one `claude` turn. A child resumes its parent's session with
`--fork-session`, so branching off an old flag inherits that branch's context
without disturbing its siblings. The strip you see is always the lineage of the
selected card, root on the left.

## Invariants worth knowing before changing things

**The ASCII grid is the layout.** Boxes render where the model drew them. There
is no layout engine and there should not be one — it is what keeps the picture
provably faithful to the text the user can inspect.

**One renderer, in Python.** `src/sketch.py` serves both the app (server-rendered
SVG injected into the page) and the standalone CLI. Do not add a JS renderer;
there was one and it was deleted for this reason. Since diagrams are sized to a
fixed container rather than zoomed, nothing needs client-side re-rendering.

**Wobble is seeded per card.** Same card, same picture, every render.

**`repair()` before `audit()`.** Models decide well what belongs in a diagram and
count characters badly, and a box whose label overruns its border does not render
wonky — it vanishes silently. `repair()` straightens boxes deterministically;
only `audit(fatal_only=True)` — a box that still will not close — is worth the
~8s of a redraw turn. An oversized diagram renders as drawn.

**Do not tell the model to count characters.** An earlier prompt did, and Claude
responded by trying to write and run a Python script to lay out the ASCII. In
read-only mode that is denied, repeatedly, and a 20-second turn became 253
seconds. `repair()` does this job; the prompt must stay out of its way.

**Read-only is an allowlist, not a denylist** (`READ_ONLY_TOOLS`). Writes,
deletes, and shell redirects are refused; Claude Code separately permits shell it
can prove is read-only, so receipts may show a `sed -n`. Editing is opt-in per
remark and only that turn gets `acceptEdits`. Blocked calls stay on the receipt
marked `denied` — a receipt listing a `Write` that never happened would be the
worst possible lie for this app.

**Model and effort reach a subprocess argv.** They are chosen from
`prompts.MODELS`/`EFFORTS`, never passed through from the client.

**The parser is where the sharp edges are**, so that is what the tests cover. Any
change to grammar tolerance (`_WIRE`, `_NEEDS`, `repair`) needs a test built from
real model output, not a hand-written ideal case — every rule in there exists
because a model actually drew it that way.
