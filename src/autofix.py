"""Point a subagent at a diagram that rendered wrong and let it fix this app.

The button says the picture is wrong. Since the picture is generated from the
ascii by `asciigrid` and `sketch`, that is a bug in this repo, and the fastest
loop is to hand the agent the evidence and let it work.

It edits and commits, so the guardrails are the point:

  * it works in this repo only, never the codebase being analysed;
  * it refuses to start on a dirty tree, so a revert can never eat your work;
  * a commit happens only if the suite passes, and a failure is reverted whole;
  * it cannot reach git, and it cannot spawn subagents. Both are learned rather
    than assumed: the first real dispatch used Bash to make its own unrelated
    commit through a Sonnet subagent, past a prompt that told it not to. A
    prompt is not a guardrail;
  * it never pushes.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

from .asciigrid import audit, parse, repair

SELF = Path(__file__).resolve().parent.parent
LOG = SELF / "autofix.log"
CLAUDE = shutil.which("claude") or "claude"
MODEL, EFFORT = "opus", "high"
CLAUDE_TIMEOUT = 1800  # a real diagnosis at high effort runs past twenty minutes
TEST_TIMEOUT = 180

# An allowlist, so reaching git or a subagent is not a matter of it behaving.
# The suite is the one command it may run; everything else it must do by editing.
FIX_TOOLS = [
    "Read", "Glob", "Grep", "Edit", "Write",
    "Bash(uv run --extra dev python -m pytest:*)",
]

SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {
            "type": "string",
            "description": "One short paragraph: what was wrong and what you changed. Under 60 words.",
        },
        "changed": {"type": "boolean", "description": "true if you edited any file"},
    },
    "required": ["summary", "changed"],
    "additionalProperties": False,
}

PROMPT = """\
A diagram rendered wrong in this app, and the user pressed the bug button on it.

The picture is generated from ASCII by src/asciigrid.py (parse, repair) and
src/sketch.py (render). If the picture is wrong, one of those is wrong.

## The ascii the model drew

```
{art}
```

## What the parser made of it

{report}

## Your job

Reproduce it, find the cause, fix it. Read docs/adding-a-pattern.md first — it
describes this exact loop and where the knobs are.

- Fix the parser or the renderer. Do not edit src/prompts.py to paper over a
  parsing bug: the drawing above already exists and has to read correctly.
- Add the drawing to tests/samples/ with correct golden counts (see
  src/capture.py:header), and a unit test in tests/test_asciigrid.py for the
  mechanism if you changed one.
- `uv run --extra dev python -m pytest -q` must pass when you are done. Every
  existing sample must keep its counts — if one changes, you have broken a
  drawing that used to read correctly.
- Change nothing unrelated. You have no git and no subagents; the runner that
  dispatched you commits your work if the suite passes and reverts it whole if
  it does not, so leave the tree containing exactly your fix and nothing else.
- If the drawing already renders correctly and you cannot find a defect, change
  nothing and say so. A clean no-op is a fine outcome; a speculative edit is not.
"""


async def _run(*argv, cwd=SELF, timeout=120, stdin: str | None = None):
    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE if stdin is not None else None,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=16 * 1024 * 1024,
    )
    try:
        out, _ = await asyncio.wait_for(
            proc.communicate(stdin.encode() if stdin is not None else None), timeout
        )
    except asyncio.TimeoutError:
        proc.kill()
        return 124, "timed out"
    return proc.returncode, out.decode(errors="replace").strip()


async def _git(*args, timeout=60):
    return await _run("git", *args, timeout=timeout)


def _report(art: str) -> str:
    fixed = repair(art)
    d = parse(fixed)
    linked = {e.source for e in d.edges} | {e.target for e in d.edges}
    orphans = [n.label.replace("\n", " ") for n in d.nodes if n.id not in linked and not n.parent]
    lines = [
        f"- boxes: {len(d.nodes)} — {[n.label.replace(chr(10), ' ')[:40] for n in d.nodes]}",
        f"- edges: {len(d.edges)}",
        f"- loose text the parser could not place: {[n.text[:40] for n in d.notes]}",
        f"- boxes nothing connects to: {orphans or 'none'}",
        f"- audit: {audit(fixed) or 'no complaints'}",
        f"- repair changed the drawing: {fixed != art}",
    ]
    return "\n".join(lines)


def _log(fix_id: str, status: str, detail: str):
    with LOG.open("a") as fh:
        fh.write(f"{fix_id[:8]}  {status:<10} {detail}\n")


async def run(fix_id: str, card: dict, record) -> None:
    """Drive one attempt. `record(status, **fields)` persists progress."""
    art = repair(card.get("ascii") or "")
    if not art.strip():
        record("failed", note="that card has no diagram to debug")
        return

    code, dirty = await _git("status", "--porcelain")
    if code != 0:
        record("failed", note=f"not a git repo: {dirty[:200]}")
        return
    if dirty:
        record("failed", note="working tree is dirty — commit or stash first, then try again")
        _log(fix_id, "refused", "dirty tree")
        return

    argv = [
        CLAUDE, "-p",
        "--output-format", "json",
        "--model", MODEL,
        "--effort", EFFORT,
        "--json-schema", json.dumps(SCHEMA),
        "--allowed-tools", ",".join(FIX_TOOLS),
        "--disallowed-tools", "Task",
        "--disable-slash-commands",
        "--add-dir", str(SELF),
    ]
    prompt = PROMPT.format(art=art, report=_report(art))
    code, out = await _run(*argv, timeout=CLAUDE_TIMEOUT, stdin=prompt)

    if code != 0:
        # A run that did not finish cleanly has not decided anything. Committing
        # a half-finished edit under a summary nobody wrote is worse than losing it.
        await _revert()
        record("failed", note=f"claude exited {code}; working tree reverted\n\n{out[-400:]}")
        _log(fix_id, "reverted", f"claude exited {code}")
        return

    payload, cost, ms = {}, 0.0, 0
    try:
        result = json.loads(out.splitlines()[-1]) if out else {}
        payload = result.get("structured_output") or {}
        cost = result.get("total_cost_usd") or 0.0
        ms = result.get("duration_ms") or 0
    except (json.JSONDecodeError, IndexError):
        pass
    summary = (payload.get("summary") or out[-400:] or "claude returned nothing").strip()

    code, changed = await _git("status", "--porcelain")
    if not changed:
        record("no_change", note=summary, cost_usd=cost, duration_ms=ms)
        _log(fix_id, "no-change", summary[:160])
        return

    ok, test_out = await _run(
        "uv", "run", "--extra", "dev", "python", "-m", "pytest", "-q", timeout=TEST_TIMEOUT
    )
    if ok != 0:
        await _revert()
        tail = test_out.strip().splitlines()[-6:]
        record("failed", note=f"{summary}\n\nreverted, tests failed:\n" + "\n".join(tail),
               cost_usd=cost, duration_ms=ms)
        _log(fix_id, "reverted", "tests failed")
        return

    await _git("add", "-A")
    subject = summary.splitlines()[0][:68] if summary else f"repair {card.get('title') or 'a diagram'}"
    message = (
        f"autofix: {subject}\n\n{summary}\n\n"
        f"Dispatched from the bug button on card {card.get('title') or card['id']}.\n"
        f"Tests passed before this was committed.\n\n"
        "Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
    )
    code, commit_out = await _git("commit", "-m", message)
    if code != 0:
        record("failed", note=f"{summary}\n\ncommit failed: {commit_out[:300]}",
               cost_usd=cost, duration_ms=ms)
        return
    _, sha = await _git("rev-parse", "--short", "HEAD")
    _, listing = await _git("show", "--name-only", "--pretty=format:", "HEAD")
    files = [f for f in listing.splitlines() if f.strip()]
    record("fixed", note=summary, commit_sha=sha, files=files, cost_usd=cost, duration_ms=ms)
    _log(fix_id, "fixed", f"{sha}  {summary[:140]}")


async def _revert():
    """Back to the last commit. Safe only because a dirty tree refused to start."""
    await _git("checkout", "--", ".")
    await _git("clean", "-fd")
