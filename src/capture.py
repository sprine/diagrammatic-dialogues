"""Collect a real diagram from Claude and file it as a parser sample.

Every tolerance in `asciigrid` exists because a model actually drew something
that way. Hand-written test fixtures encode what we *imagine* a model does, so
they pass while the real thing breaks. This runs one honest turn through the same
argv the app uses and saves the ascii exactly as it came back, un-repaired.

    uv run python -m src.capture fanin --dir . --ask "How does SSE reach the browser?"
    uv run python -m src.capture layout --dir ~/some/repo --model opus --effort high
"""

from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from . import claude_cli
from .asciigrid import audit, parse, repair
from .prompts import root_prompt
from .sketch import render

SAMPLES = Path(__file__).resolve().parent.parent / "tests" / "samples"


async def _draw(prompt: str, target: Path, model: str, effort: str) -> str:
    async for event in claude_cli.one_turn(
        prompt=prompt, target=target, model=model, effort=effort, write=False, resume=None
    ):
        if event["kind"] == "activity":
            print(f"  · {event['tool']} {event['detail'][:70]}")
        elif event["kind"] == "error":
            raise SystemExit(f"claude failed: {event['message'][:400]}")
        elif event["kind"] == "result":
            return event["result"]["ascii"]
    raise SystemExit("claude returned nothing")


def orphans(diagram) -> list[str]:
    """Boxes nothing connects to — usually a connector the parser could not follow."""
    linked = {e.source for e in diagram.edges} | {e.target for e in diagram.edges}
    return [n.label.replace("\n", " ") for n in diagram.nodes if n.id not in linked and not n.parent]


def header(repaired: str) -> str:
    """The golden counts a sample is pinned to. Record reality, including gaps."""
    d = parse(repaired)
    return f"# boxes={len(d.nodes)} edges={len(d.edges)} orphans={len(orphans(d))}"


def report(art: str) -> tuple[str, list[str]]:
    """What the parser makes of a drawing, before and after repair."""
    raw = parse(art)
    print(f"  raw      {len(raw.nodes)} boxes, {len(raw.edges)} edges, "
          f"{len(audit(art, fatal_only=True))} fatal")
    fixed_art = repair(art)
    fixed = parse(fixed_art)
    problems = audit(fixed_art, fatal_only=True)
    print(f"  repaired {len(fixed.nodes)} boxes, {len(fixed.edges)} edges, {len(problems)} fatal")
    for note in fixed.notes:
        print(f"  loose text: {note.text[:60]!r}")
    return fixed_art, problems


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("name", help="sample filename, without extension")
    ap.add_argument("--dir", default=".", help="codebase to draw")
    ap.add_argument("--ask", help="a remark; omitted means the opening high-level map")
    ap.add_argument("--model", default="sonnet")
    ap.add_argument("--effort", default="medium")
    args = ap.parse_args()

    target = Path(args.dir).expanduser().resolve()
    prompt = args.ask or root_prompt(str(target))
    print(f"drawing {target} with {args.model}/{args.effort}…")
    art = asyncio.run(_draw(prompt, target, args.model, args.effort))

    print("\n" + art)
    fixed_art, problems = report(art)

    SAMPLES.mkdir(parents=True, exist_ok=True)
    path = SAMPLES / f"{args.name}.txt"
    path.write_text(header(fixed_art) + "\n" + art)
    preview = Path("/tmp") / f"{args.name}.svg"
    preview.write_text(render(parse(fixed_art), seed=args.name, width=1200, height=900,
                              ratio=0.95, standalone=True))

    print(f"\nsaved  {path}")
    print(f"render {preview}   <- open this and check it says what the code does")
    if problems:
        print("\nrepair could not close every box:")
        for p in problems:
            print(f"  - {p}")
        print("That is the interesting case. Fix asciigrid until this is empty,")
        print("then correct the counts in the sample header.")


if __name__ == "__main__":
    main()
