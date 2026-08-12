"""The contract with Claude: draw in ASCII, answer in prose, cite real files.

ASCII is the constraint that keeps diagrams simple. A grid 78 columns wide has
room for about nine boxes, so the model must decide what matters instead of
transcribing the whole repo.
"""

from __future__ import annotations

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string", "description": "3-6 words naming this diagram"},
        "ascii": {"type": "string", "description": "the ASCII diagram, obeying the drawing rules"},
        "answer": {
            "type": "string",
            "description": "At most 90 words. What the diagram shows and what you verified, citing file paths.",
        },
    },
    "required": ["title", "ascii", "answer"],
    "additionalProperties": False,
}

DRAWING_RULES = """\
You answer with a diagram. The diagram is drawn as ASCII art and rendered as a
hand-drawn sketch, so it must parse mechanically. Obey these rules exactly.

GRID
- At most 78 columns wide and 26 rows tall. Pad with spaces, never tabs.
- At most 9 boxes. If the subject has more parts, group or omit — a diagram that
  does not fit is a diagram that was not thought through.

BOXES
- Corners are `+`, horizontal runs are `-`, vertical runs are `|`. Nothing else.
- Draw each box as a rectangle, sizing it from its longest label line. Line the
  `|` up with the `+` above and below by eye and move on: a box that is off by a
  column or two is straightened automatically before anyone sees it. Do not
  spend the turn counting characters, and never write a file or run a script to
  lay the diagram out — type it directly into your answer.
- A box label is 1-3 lines, at most 18 characters per line, padded with spaces.
- Separate boxes by at least one blank column and one blank row.
- A box may enclose other boxes to show grouping. Put the group's own label on
  its first inner line, and keep the children clear of it.

CONNECTORS
- Horizontal and vertical runs, turning corners with `+`. For fanning several
  boxes into one, `\` and `/` are allowed, each continuing on its own slope.
- Arrowheads are `>` `<` `^` `v` and must sit immediately against the box border
  they point into: `--->|` not `---> |`.
- A connector may carry one short label written inline between dashes:
  `---- writes ---->`. Never write any other text on a connector.
- Connectors must not pass through a box.

GOOD
+-----------+   parses  +--------------+
|  cli.py   |---------->|  planner.py  |
+-----------+           +--------------+
                               |
                               v
                        +--------------+
                        |  executor.py |
                        +--------------+

BAD  (arrow detached, label loose on the line, box overflowing)
+-----------+           +--------------+
|  cli.py   | ---> reads |  planner.py |
+-----------+           +--------------+

ANSWER
- 90 words at the outside. Plain and specific. Name the real files you read.
  The diagram carries the structure; the answer carries only what it cannot.
- Say what you verified and what you are inferring. If the code does something
  the obvious design would not, say so — that is the whole point of this tool.
- Never claim something you did not read. "I did not check X" is a good answer.
"""

_ROOT_TASK = """\
Map the code in {target} as a high-level diagram.

Move fast and stay shallow: read the entry points, the directory layout, and
enough of the main modules to be honest about the shape. This is the opening
sketch, not an audit — the user will point at whatever looks wrong.

Show the major components and how control or data moves between them.
"""

_REMARK_TASK = """\
The user is looking at your diagram "{parent_title}" and has planted a flag on
{where}.

{around}

Their remark:
{remark}

Answer it by producing the next diagram. This diagram replaces the previous one:
redraw at whatever altitude the remark calls for — usually deeper and narrower,
zoomed into the part they pointed at. Read the actual code before answering.

Here is the diagram they are pointing at, for reference:
{parent_ascii}
"""

WRITE_NOTE = """\
You may modify files in the target directory for this turn, because the user
asked you to build rather than explain. Make the smallest change that satisfies
the remark. Then draw the diagram of what the code looks like *after* your
change, and list every file you touched in your answer.
"""


def root_prompt(target: str) -> str:
    return _ROOT_TASK.format(target=target)


def remark_prompt(parent: dict, remark: str, anchor_label: str, neighbours: list[str]) -> str:
    where = f'the box labelled "{anchor_label}"' if anchor_label else "the diagram as a whole"
    around = (
        f"Directly connected to it: {', '.join(neighbours)}." if neighbours else "It stands alone in the diagram."
    )
    return _REMARK_TASK.format(
        parent_title=parent.get("title") or "untitled",
        where=where,
        around=around,
        remark=remark.strip(),
        parent_ascii=parent.get("ascii", ""),
    )


# Sketch: "start with low effort; future effort increases with depth".
LADDER = [("sonnet", "low"), ("sonnet", "medium"), ("sonnet", "medium"), ("opus", "high")]
MODELS = ["haiku", "sonnet", "opus"]
EFFORTS = ["low", "medium", "high", "xhigh"]


def rung(depth: int) -> tuple[str, str]:
    return LADDER[min(depth, len(LADDER) - 1)]
