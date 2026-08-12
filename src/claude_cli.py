"""Driver for the `claude` CLI. Every diagram in this app comes from here.

One card == one CLI turn. A child card resumes its parent's session and forks,
so branching off an old flag inherits that branch's context without disturbing
its siblings.
"""

from __future__ import annotations

import asyncio
import json
import re
import shutil
from pathlib import Path

from .asciigrid import audit, repair
from .prompts import DRAWING_RULES, OUTPUT_SCHEMA, WRITE_NOTE

CLAUDE = shutil.which("claude") or "claude"
STREAM_LIMIT = 16 * 1024 * 1024  # stream-json lines carry whole file reads

# Read-only turns get an allowlist, not a denylist: anything not named is denied,
# so there is no way to reach a mutating command by a route we forgot about.
READ_ONLY_TOOLS = [
    "Read", "Glob", "Grep",
    "Bash(git log:*)", "Bash(git show:*)", "Bash(git diff:*)", "Bash(git blame:*)",
    "Bash(git status:*)", "Bash(ls:*)", "Bash(find:*)", "Bash(wc:*)", "Bash(rg:*)",
]

_DETAIL = {
    "Read": lambda i: i.get("file_path", ""),
    "Edit": lambda i: i.get("file_path", ""),
    "Write": lambda i: i.get("file_path", ""),
    "NotebookEdit": lambda i: i.get("notebook_path", ""),
    "Glob": lambda i: i.get("pattern", ""),
    "Grep": lambda i: " ".join(filter(None, [i.get("pattern", ""), i.get("path", "")])),
    "Bash": lambda i: i.get("command", ""),
    "Task": lambda i: i.get("description", ""),
    "WebFetch": lambda i: i.get("url", ""),
}
WRITING_TOOLS = {"Edit", "Write", "NotebookEdit"}

# Models occasionally close a tag they never opened; it should not reach the card.
_SCAFFOLD = re.compile(r"</?(answer|invoke|function_calls|parameter|result|output)[^>]*>")


def _argv(*, model: str, effort: str, target: Path, write: bool, resume: str | None) -> list[str]:
    argv = [
        CLAUDE, "-p",
        "--output-format", "stream-json", "--verbose",
        "--model", model,
        "--effort", effort,
        "--include-partial-messages",  # so a long silent think still shows something
        "--json-schema", json.dumps(OUTPUT_SCHEMA),
        "--append-system-prompt", DRAWING_RULES + (WRITE_NOTE if write else ""),
        "--disable-slash-commands",
        "--add-dir", str(target),
    ]
    if write:
        argv += ["--permission-mode", "acceptEdits"]
    else:
        argv += ["--allowed-tools", ",".join(READ_ONLY_TOOLS)]
    if resume:
        argv += ["--resume", resume, "--fork-session"]
    return argv


REDRAW = """\
Your diagram does not parse, so parts of it would be missing from what the user sees:

{problems}

Every `|` must sit in the same column as the `+` above and below it, so a label
has to be shorter than the border holding it. Widen the box, or shorten the
label, or drop the box if it was not earning its place.

Redraw the whole diagram inline — no files, no scripts. Same content, correct
geometry. Keep your answer text as it was unless the redraw changes what is true.
"""


async def run(*, prompt: str, target: Path, model: str, effort: str, write: bool, resume: str | None):
    """Yield progress events, then exactly one 'done' or 'error' event.

    A drawing that fails to parse gets one redraw, continuing the same session so
    Claude still has the code it just read in context.
    """
    evidence: list[dict] = []
    changes: list[str] = []
    final: dict | None = None
    cost = elapsed = 0.0

    for attempt in range(2):
        async for event in one_turn(
            prompt=prompt, target=target, model=model, effort=effort,
            write=write, resume=resume,
        ):
            if event["kind"] == "result":
                final = event["result"]
                evidence.extend(event["evidence"])
                changes.extend(c for c in event["changes"] if c not in changes)
                cost += final["cost_usd"]
                elapsed += final["duration_ms"]
            elif event["kind"] == "error":
                yield {**event, "evidence": evidence + event.get("evidence", [])}
                return
            else:
                yield event

        if final is None:
            break
        # Straighten what can be straightened before spending a turn on a redraw,
        # and only spend one when boxes would actually be missing from the picture.
        final["ascii"] = repair(final["ascii"])
        problems = audit(final["ascii"], fatal_only=True)
        if not problems or attempt == 1:
            break
        yield {"kind": "activity", "tool": "redraw", "detail": problems[0]}
        prompt = REDRAW.format(problems="\n".join(f"- {p}" for p in problems))
        resume = final["session_id"]
        write = False  # the redraw is a drawing fix, never another pass at the code

    if final is None:
        yield {"kind": "error", "message": "claude returned nothing", "evidence": evidence}
        return

    yield {
        "kind": "done",
        "title": final["title"],
        "ascii": final["ascii"],
        "answer": final["answer"],
        "evidence": evidence,
        "changes": changes,
        "session_id": final["session_id"],
        "cost_usd": cost,
        "duration_ms": int(elapsed),
    }


async def one_turn(*, prompt: str, target: Path, model: str, effort: str, write: bool, resume: str | None):
    """One CLI invocation: activity events, then a single 'result' or 'error'.

    The ascii comes back exactly as the model drew it — no repair, no redraw.
    `src/capture.py` depends on that to collect honest parser samples.
    """
    argv = _argv(model=model, effort=effort, target=target, write=write, resume=resume)
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=str(target),
            limit=STREAM_LIMIT,
        )
    except OSError as exc:
        yield {"kind": "error", "message": f"could not start claude: {exc}"}
        return

    yield {"kind": "proc", "proc": proc}

    proc.stdin.write(prompt.encode())
    await proc.stdin.drain()
    proc.stdin.close()

    evidence: list[dict] = []
    changes: list[str] = []
    result: dict | None = None
    murmur, unsent, drawing = "", 0, False

    async for event in _lines(proc.stdout):
        kind = event.get("type")
        if kind == "stream_event":
            inner = event.get("event", {})
            step = inner.get("type")
            if step == "content_block_start":
                block = inner.get("content_block", {})
                drawing = block.get("name") == "StructuredOutput"
                continue
            if step == "content_block_stop":
                drawing = False
                continue
            if step != "content_block_delta":
                continue
            delta = inner.get("delta", {})
            # While drawing, partial_json is the diagram being written — the only
            # sign of life on a turn that reads nothing and goes straight to it.
            piece = delta.get("thinking") or delta.get("text") or ""
            if not piece and drawing:
                piece = _unescape(delta.get("partial_json"))
            if not piece:
                continue
            murmur, unsent = (murmur + piece)[-400:], unsent + len(piece)
            if unsent >= 70:  # a readable trickle, not a firehose
                unsent = 0
                yield {"kind": "note", "text": " ".join(murmur.split())[-140:]}
        elif kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") != "tool_use":
                    continue
                name = block.get("name", "")
                if name == "StructuredOutput":
                    continue
                detail = _relative(_DETAIL.get(name, lambda i: "")(block.get("input") or {}), target)
                if evidence and (evidence[-1]["tool"], evidence[-1]["detail"]) == (name, detail):
                    continue
                evidence.append({"tool": name, "detail": detail, "id": block.get("id")})
                yield {"kind": "activity", "tool": name, "detail": detail}
        elif kind == "result":
            result = event

    stderr = (await proc.stderr.read()).decode(errors="replace").strip()
    await proc.wait()

    if result is None or result.get("is_error"):
        message = (result or {}).get("result") or stderr or f"claude exited {proc.returncode}"
        yield {"kind": "error", "message": str(message)[:2000], "evidence": evidence}
        return

    # A blocked call must never read as a completed one on the receipt.
    denied = {d.get("tool_use_id") for d in result.get("permission_denials") or []}
    for step in evidence:
        step["denied"] = step.pop("id", None) in denied
    changes = [
        s["detail"] for s in evidence
        if s["tool"] in WRITING_TOOLS and s["detail"] and not s["denied"]
    ]

    payload = result.get("structured_output") or _salvage(result.get("result"))
    if not payload:
        yield {
            "kind": "error",
            "message": "claude returned no diagram:\n" + str(result.get("result"))[:1500],
            "evidence": evidence,
        }
        return

    yield {
        "kind": "result",
        "evidence": evidence,
        "changes": changes,
        "result": {
            "title": _clean(payload.get("title")) or "untitled",
            "ascii": payload.get("ascii") or "",
            "answer": _clean(payload.get("answer")),
            "session_id": result.get("session_id"),
            "cost_usd": result.get("total_cost_usd") or 0.0,
            "duration_ms": result.get("duration_ms") or 0,
        },
    }


async def _lines(stream):
    while True:
        try:
            raw = await stream.readline()
        except (asyncio.LimitOverrunError, ValueError):
            continue  # one oversized line is not worth failing the turn over
        if not raw:
            return
        line = raw.decode(errors="replace").strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


_ESCAPES = {"\\n": " ", "\\t": " ", '\\"': '"', "\\\\": "\\", "\\/": "/"}


def _unescape(fragment: str | None) -> str:
    """Streamed tool arguments arrive as raw JSON; show the words, not the syntax."""
    if not fragment:
        return ""
    for old, new in _ESCAPES.items():
        fragment = fragment.replace(old, new)
    return fragment.translate(str.maketrans("", "", '{}[]"'))


def _clean(text) -> str:
    return _SCAFFOLD.sub("", text or "").strip()


def _salvage(text) -> dict | None:
    """Structured output usually lands; when it doesn't, a bare JSON object in
    the text is still worth reading rather than losing the whole turn."""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        obj = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) and "ascii" in obj else None


def _relative(detail: str, target: Path) -> str:
    if not detail:
        return ""
    try:
        return str(Path(detail).relative_to(target)) if detail.startswith("/") else detail
    except ValueError:
        return detail
