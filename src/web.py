"""Speak to me in Diagrams — server.

State lives in SQLite; live turns live in JOBS. A card's lineage (root -> card)
is the horizontal strip the user scrolls, and every card's children are the
flags planted on it.
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import uuid
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import claude_cli, prompts, sketch
from .asciigrid import parse, repair
from .models import card_row, children, db, init_schema, lineage

ACTIVE = {"width": 960, "height": 540, "ratio": 0.6}
MINI = {"width": 300, "height": 200, "ratio": 0.95}

ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = Jinja2Templates(directory=str(ROOT / "templates"))
DEFAULT_PORT = 8420


class Job:
    """A running turn. Buffers events so a late SSE subscriber sees the whole run."""

    def __init__(self):
        self.events: list[dict] = []
        self.subscribers: set[asyncio.Queue] = set()
        self.proc = None
        self.finished = False

    def emit(self, event: dict):
        self.events.append(event)
        for q in self.subscribers:
            q.put_nowait(event)

    async def subscribe(self):
        q: asyncio.Queue = asyncio.Queue()
        for event in self.events:
            q.put_nowait(event)
        self.subscribers.add(q)
        try:
            while True:
                event = await q.get()
                yield event
                if event["kind"] in ("done", "error"):
                    return
        finally:
            self.subscribers.discard(q)


JOBS: dict[str, Job] = {}


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_schema()
    with db() as conn:  # a turn cannot survive a restart; do not leave ghosts
        conn.execute(
            "UPDATE card SET status='error', error='interrupted by restart' WHERE status='running'"
        )
    yield


app = FastAPI(lifespan=lifespan, title="Speak to me in Diagrams")
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")


@app.get("/")
def index(request: Request):
    return TEMPLATES.TemplateResponse(
        request,
        "index.html",
        {
            "cwd": os.getcwd(),
            "models": prompts.MODELS,
            "efforts": prompts.EFFORTS,
            "ladder": prompts.LADDER,
        },
    )


@app.get("/api/trails")
def api_trails():
    with db() as conn:
        rows = conn.execute(
            """SELECT t.*, (SELECT COUNT(*) FROM card WHERE trail_id = t.id) AS cards,
                      (SELECT id FROM card WHERE trail_id = t.id AND parent_id IS NULL) AS root_id
               FROM trail t ORDER BY t.created_at DESC"""
        ).fetchall()
    return [dict(r) for r in rows]


@app.post("/api/trails")
async def api_open(payload: dict = Body(...)):
    target = Path(payload.get("target_dir", "")).expanduser()
    if not target.is_dir():
        raise HTTPException(400, f"not a directory: {target}")
    target = target.resolve()

    trail_id, card_id = str(uuid.uuid4()), str(uuid.uuid4())
    model, effort = prompts.rung(0)
    with db() as conn:
        conn.execute(
            "INSERT INTO trail (id, target_dir, title) VALUES (?,?,?)",
            (trail_id, str(target), target.name),
        )
        conn.execute(
            "INSERT INTO card (id, trail_id, depth, model, effort, remark) VALUES (?,?,0,?,?,?)",
            (card_id, trail_id, model, effort, f"Map {target.name}"),
        )
    _launch(
        card_id,
        prompt=prompts.root_prompt(str(target)),
        target=target,
        model=model,
        effort=effort,
        write=False,
        resume=None,
    )
    return {"trail_id": trail_id, "card_id": card_id}


@app.post("/api/cards/{card_id}/remark")
async def api_remark(card_id: str, payload: dict = Body(...)):
    remark = (payload.get("remark") or "").strip()
    if not remark:
        raise HTTPException(400, "remark is empty")

    with db() as conn:
        row = conn.execute("SELECT * FROM card WHERE id = ?", (card_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such card")
        parent = card_row(row)
        trail = conn.execute(
            "SELECT * FROM trail WHERE id = ?", (parent["trail_id"],)
        ).fetchone()

    # These reach a subprocess argv, so they are chosen from the list, not passed through.
    depth = parent["depth"] + 1
    default_model, default_effort = prompts.rung(depth)
    model = payload.get("model") if payload.get("model") in prompts.MODELS else default_model
    effort = payload.get("effort") if payload.get("effort") in prompts.EFFORTS else default_effort
    write = bool(payload.get("write"))

    # Must match what api_view rendered, or the flag resolves to the wrong box.
    diagram = parse(repair(parent["ascii"]))
    anchor_node = payload.get("anchor_node")
    node = diagram.node(anchor_node) if anchor_node else None
    anchor_label = node.label.replace("\n", " ") if node else ""
    neighbours = diagram.neighbours(anchor_node) if node else []

    child_id = str(uuid.uuid4())
    with db() as conn:
        conn.execute(
            """INSERT INTO card (id, trail_id, parent_id, depth, remark, anchor_node,
                                 anchor_label, model, effort, write_mode)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (child_id, parent["trail_id"], card_id, depth, remark, anchor_node,
             anchor_label, model, effort, int(write)),
        )
    _launch(
        child_id,
        prompt=prompts.remark_prompt(parent, remark, anchor_label, neighbours),
        target=Path(trail["target_dir"]),
        model=model,
        effort=effort,
        write=write,
        resume=parent["session_id"],
    )
    return {"card_id": child_id}


@app.get("/api/view/{card_id}")
def api_view(card_id: str):
    with db() as conn:
        chain = lineage(conn, card_id)
        if not chain:
            raise HTTPException(404, "no such card")
        trail = dict(conn.execute("SELECT * FROM trail WHERE id = ?", (chain[0]["trail_id"],)).fetchone())
        for i, card in enumerate(chain):
            active = i == len(chain) - 1
            card["flags"] = [
                {
                    "card_id": kid["id"],
                    "n": n + 1,
                    "remark": kid["remark"],
                    "anchor_node": kid["anchor_node"],
                    "anchor_label": kid["anchor_label"],
                    "status": kid["status"],
                    "on_path": any(c["id"] == kid["id"] for c in chain),
                    "write_mode": kid["write_mode"],
                }
                for n, kid in enumerate(children(conn, card["id"]))
            ]
            # Straighten on the way out too, so a parser improvement repairs
            # diagrams drawn before it existed. What the ASCII toggle shows is
            # what the picture was built from — they can never disagree.
            card["ascii"] = repair(card["ascii"])
            card["svg"] = sketch.render(
                parse(card["ascii"]),
                **(ACTIVE if active else MINI),
                seed=card["id"],
                flags=card["flags"],
                interactive=active,
            )
    return {"trail": trail, "cards": chain, "selected": card_id}


@app.get("/api/stream/{card_id}")
async def api_stream(card_id: str):
    job = JOBS.get(card_id)

    async def body():
        if not job:
            yield _sse({"kind": "idle"})
            return
        async for event in job.subscribe():
            yield _sse(event)

    return StreamingResponse(
        body(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cards/{card_id}/cancel")
def api_cancel(card_id: str):
    job = JOBS.get(card_id)
    if job and job.proc and job.proc.returncode is None:
        job.proc.kill()
    return {"ok": True}


@app.delete("/api/trails/{trail_id}")
def api_delete(trail_id: str):
    with db() as conn:
        conn.execute("DELETE FROM trail WHERE id = ?", (trail_id,))
    return {"ok": True}


@app.exception_handler(HTTPException)
def http_error(_: Request, exc: HTTPException):
    return JSONResponse({"error": exc.detail}, status_code=exc.status_code)


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _launch(card_id: str, **kwargs):
    job = Job()
    JOBS[card_id] = job
    asyncio.create_task(_drive(card_id, job, **kwargs))


async def _drive(card_id: str, job: Job, **kwargs):
    try:
        async for event in claude_cli.run(**kwargs):
            if event["kind"] == "proc":
                job.proc = event["proc"]
                continue
            if event["kind"] in ("done", "error"):
                _persist(card_id, event)
            job.emit(event)
    except Exception as exc:  # a broken turn must not take the server with it
        event = {"kind": "error", "message": f"{type(exc).__name__}: {exc}", "evidence": []}
        _persist(card_id, event)
        job.emit(event)
    finally:
        job.finished = True


def _persist(card_id: str, event: dict):
    with db() as conn:
        if event["kind"] == "error":
            conn.execute(
                "UPDATE card SET status='error', error=?, evidence=? WHERE id=?",
                (event.get("message", "")[:4000], json.dumps(event.get("evidence", [])), card_id),
            )
            return
        conn.execute(
            """UPDATE card SET status='done', title=?, ascii=?, answer=?, evidence=?,
                               changes=?, session_id=?, cost_usd=?, duration_ms=?
               WHERE id=?""",
            (
                event["title"], event["ascii"], event["answer"],
                json.dumps(event["evidence"]), json.dumps(event["changes"]),
                event["session_id"], event["cost_usd"], event["duration_ms"], card_id,
            ),
        )


def _pick_port(preferred: int) -> int:
    for candidate in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", candidate))
                return s.getsockname()[1]
            except OSError:
                continue
    raise RuntimeError("no free port")


def main():
    port = _pick_port(int(os.environ.get("SID_PORT", DEFAULT_PORT)))
    url = f"http://127.0.0.1:{port}"
    print(f"Speak to me in Diagrams -> {url}", flush=True)
    if os.environ.get("SID_OPEN", "1") == "1":
        webbrowser.open(url)
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    main()
