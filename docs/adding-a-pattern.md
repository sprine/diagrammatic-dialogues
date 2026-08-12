# Adding a pattern

A *pattern* is a shape you want Claude to be able to draw: a fan-in bus, a
decision branch, a swimlane, a nested group. Adding one touches two things that
must agree — the worked examples in `DRAWING_RULES`, which teach the model, and
`asciigrid.py`, which has to read back what the model draws. Teaching a shape the
parser cannot read produces diagrams with pieces silently missing, which is the
one failure this app must never ship.

The order below exists because of that. Prove the parser first, teach second,
then check what the model actually does with it.

## 1. Write the pattern and prove the parser reads it

Draw the shape by hand and run it through the parser before touching any prompt.

```python
from src.asciigrid import parse, repair, audit
art = """
 +--------+  +--------+
 | puzzle |  |  spot  |
 +---+----+  +---+----+
     \\           /
      \\_________/
           |
           v
   +---------------+
   | gallery store |
   +---------------+
"""
d = parse(repair(art))
print(len(d.nodes), len(d.edges), audit(repair(art), fatal_only=True))
print([(e.source, e.target, e.label) for e in d.edges])
```

You want the box count you drew, the edges you meant, no fatal problems, and
nothing you intended as structure sitting in `d.notes` — a connector that ended
up as a note is a connector the renderer will draw as loose grey text.

If it does not read correctly, fix `asciigrid.py` now. The knobs, roughly:

| symptom | look at |
|---|---|
| a character is ignored entirely | `_WIRE` |
| a character is dropped as "not part of a structure" | `_NEEDS` |
| diagonals do not join up | `_SLOPE`, `_linked` |
| text on a connector splits it in two | `_INLINE_LABEL`, `_caption_span` |
| text on a connector disappears | `_stranded` — no edge claimed the label |
| a box does not close | `repair`, `_first_broken_box` |

Every entry in those is there because a model really drew it that way. Keep that
bar: add a tolerance only when you have output that needs it.

## 2. Add the worked example to the prompt

Put it in `DRAWING_RULES` in `src/prompts.py`, in the `GOOD` block, labelled with
what it is for. Use the exact text you just proved — do not retype it, since a
one-character drift makes the example unparseable and you will have taught the
model to draw something the app cannot read.

Watch the escaping: `DRAWING_RULES` is a normal Python string, so a backslash in
a diagonal must be written `\\`.

Keep examples few and short. They are prepended to every turn, so each one costs
tokens on every call forever, and a wall of examples reads as noise rather than
instruction.

**Do not describe the pattern in terms of character counts.** An earlier version
of these rules said "count the characters", and Claude responded by trying to
write and run a Python script to lay the diagram out. Read-only mode denied it,
repeatedly, and a 20-second turn became 253 seconds. `repair()` already fixes
alignment; the prompt's job is to say what the shape *means*, not to make the
model do arithmetic.

## 3. Capture what the model actually draws

An example is a hypothesis. Test it against the real thing:

```bash
uv run python -m src.capture fan-in --dir . \
  --ask "Which modules read from src/asciigrid.py? Show them fanning into it."
```

This runs one real turn through the same argv the app uses — same schema, same
system prompt, same allowlist — and saves the ascii exactly as it came back,
un-repaired, to `tests/samples/<name>.txt`. It prints the parse before and after
repair, lists any loose text, and writes an SVG preview to `/tmp/<name>.svg`.

**Open the preview.** The counts can be right while the picture is wrong. You are
checking two different things: that the parser read the drawing, and that the
drawing says what the code does.

Expect the model to ignore your example sometimes. `tests/samples/loose-diagonals.txt`
is a capture from immediately after the fan-in example was added, where it fanned
the other way with diagonals on no consistent slope, and two boxes came back
unconnected. That is recorded rather than deleted — see below.

## 4. Pin the sample

Each sample carries a header of golden counts:

```
# boxes=5 edges=4 orphans=0
```

`tests/test_samples.py` asserts all three, plus that no box goes missing after
repair. The counts are pinned deliberately: if a parser change starts reading
*more* of a drawing, the test fails and you update the header on purpose, having
looked at why.

`orphans` is the honest part. A non-zero count is a shape the parser cannot
follow — a gap you are choosing not to close yet, recorded so it cannot be
mistaken for success and so anyone who fixes it sees the number drop.

Regenerate a header after an intentional parser change:

```python
from pathlib import Path
from src.asciigrid import repair
from src.capture import header
for p in Path("tests/samples").glob("*.txt"):
    body = p.read_text().split("\n", 1)[1]
    p.write_text(header(repair(body)) + "\n" + body)
```

## 5. Add a unit test for the mechanism

The sample pins behaviour; a unit test in `tests/test_asciigrid.py` explains it.
Write the smallest drawing that exercises the rule, and assert what the rule is
*for* — the edge that survives, the label that lands, the note that does not
appear. Include the counter-case where it should *not* fire: when caption
bridging was added, the test that mattered was the one proving text under a
horizontal arrow is still that arrow's own label and not joined downward.

## 6. Run everything

```bash
uv run --extra dev python -m pytest -q
```

The corpus is cheap to run and every sample is a real drawing, so a green suite
means no shape anyone has ever captured got worse.

---

Two things worth internalising, because they are why this procedure is shaped the
way it is. The parser is tolerant on purpose — models are good at deciding what
belongs in a diagram and bad at drawing it precisely, and the cost of tolerance
is a false connector while the cost of strictness is a missing box, which is much
worse. And prompt changes are cheap to make and expensive to verify: the only
evidence that an example worked is a capture, so budget a real turn for it.
