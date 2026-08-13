# Diagramatic Dialogues

Open a codebase, get a sketch of it, and point at anything that looks wrong.
Every question you ask leaves a numbered flag on the diagram it was asked about,
and clicking a flag opens the diagram that answered it.

Built for one question: *I did not write this code — is that the decision I
would have taken?*

## Running it

Needs [`uv`](https://docs.astral.sh/uv/) and a logged-in `claude` CLI.

```bash
./run.sh                 # opens http://127.0.0.1:8420
```

`SID_PORT` picks the port, `SID_OPEN=0` stops it opening a browser, `SID_DB`
moves the database.

## The loop

1. **Open** a directory. Claude reads it and draws a high-level diagram, fast
   and shallow — this is the opening sketch, not an audit.
2. **Plant a flag.** Click any box to aim at it, then ask a question or give an
   instruction. Where you clicked is carried along with the question, along with
   what that box connects to.
3. **A new diagram replaces it**, redrawn at whatever altitude the remark called
   for. The previous diagrams stay on screen to the left as minimaps.
4. **Every flag stays clickable**, so the whole trail of what you asked remains
   visible and navigable. Asking a second question of an older diagram branches
   from it rather than overwriting the first.

The strip is always the lineage of the diagram you are looking at, root on the
left. Scroll left through the history; click any older card to make it current.

## Why you can trust what you see

The app exists to answer questions about code you did not write, so it tries not
to ask for faith:

- **You can watch it work.** A running card shows an elapsed clock, each file it
  opens as it opens it, and a live line of what it is thinking or drawing — so a
  turn that reads nothing and goes straight to the diagram still shows movement.
- **Every card carries a receipt** — every file read, every search run, in order,
  with cost and wall time. Blocked calls are listed and marked `blocked` rather
  than quietly dropped.
- **The ASCII source is one click away** — the `ASCII` pill in the card header
  swaps the picture for the text it was drawn from. The picture is generated
  mechanically from exactly that text, so it cannot say something the text does
  not.
- **A bad render is filed, not patched.** Beside the ASCII there is a box asking
  what the render got wrong. Say so and it lands in `training-data/` as a record
  and the wrong picture next to it: your description, the ASCII as the model drew
  it and as repair straightened it, and what the pipeline made of it. Nothing
  edits the renderer — these are the failures the next round of training needs to
  see, and quietly correcting them by hand is how they stop being visible.
- **Read-only by default.** A turn runs with an allowlist, not a denylist:
  `Read`, `Glob`, `Grep`, and a handful of read-only `git`/`ls`/`find` commands.
  Writes, deletes, and shell redirects are refused. Claude Code additionally
  permits shell commands it can prove are read-only, so the receipt may show a
  `sed -n` or an `echo`; anything that mutates is denied.
- **Editing is opt-in per question.** Tick *let it edit files* on a single
  remark and only that turn runs with `acceptEdits`. Files it changed are listed
  on the resulting card and the card is badged `wrote code`.
- **Effort climbs with depth.** The first diagram is sonnet at low effort;
  deeper questions escalate toward opus at high effort. Every card records the
  model and effort that produced it, and you can override both before asking.

## How it works

```
src/asciigrid.py   ASCII grid -> geometry, plus repair() and audit()
src/sketch.py      geometry -> hand-drawn SVG (also a CLI: python -m src.sketch)
src/claude_cli.py  drives `claude -p`, streams progress, retries a bad drawing
src/prompts.py     the drawing contract and the depth ladder
src/models.py      SQLite: a trail is a rooted tree of cards
src/web.py         FastAPI + SSE
```

One card is one `claude` turn. A child card resumes its parent's session and
forks it, so branching off an old flag inherits that branch's context without
disturbing its siblings.

Diagrams are drawn as ASCII and rendered from the grid, so the ASCII *is* the
layout — no layout engine, and the picture always matches its source. Models
miscount characters, so `repair()` straightens boxes that missed by a column
before anything is shown; `audit(fatal_only=True)` catches what it cannot fix
and spends one extra turn asking for a redraw. See
`.claude/skills/ascii-to-sketch`.

Only a box that failed to close earns that extra turn, because that is the one
failure that erases content. An oversized drawing is reported and rendered as
drawn. The prompt deliberately tells Claude *not* to count characters or reach
for a script to lay the diagram out — repair does it better, and a model trying
to build its own layout tooling in a read-only sandbox is how a 20-second turn
becomes a four-minute one.

Diagrams are sized to a fixed container rather than zoomed or panned, per the
original sketch: 60% of the card's width, bounded by its height.

## Tests

```bash
uv run --extra dev python -m pytest -q
```

The parser is where the sharp edges are, so that is what is covered.
