"""FastAPI application: auth, REST API, SSE logs, media, terminal, static frontend."""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import shutil
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response, WebSocket
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import budget, db, installer, jobq, registry, terminal
from .config import ROOT, get_settings
from .pipeline import ckpt, preflight
from .pipeline.spec import PodcastSpec, parse_instruction
from .research import Research
from .security import LoginThrottle, install_log_redaction, origin_allowed, safe_join, token_ok

log = logging.getLogger("app")
COOKIE = "pw_session"
SESSION_TTL = 12 * 3600
throttle = LoginThrottle()

@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO)
    install_log_redaction()
    db.init()
    get_settings().projects_dir.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(title="Podcast Workspace", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)


# ------------------------------------------------------------------ auth
def _sign(exp: int, nonce: str) -> str:
    return hmac.new(get_settings().owner_token.encode(), f"{exp}.{nonce}".encode(), hashlib.sha256).hexdigest()


def _make_cookie() -> str:
    exp, nonce = int(time.time()) + SESSION_TTL, os.urandom(8).hex()
    return f"{exp}.{nonce}.{_sign(exp, nonce)}"


def _cookie_ok(val: str | None) -> bool:
    try:
        exp, nonce, sig = (val or "").split(".")
        return int(exp) > time.time() and hmac.compare_digest(sig, _sign(int(exp), nonce))
    except ValueError:
        return False


def owner(request: Request) -> str:
    """Owner session cookie or Authorization: Bearer <token>. Cookie writes also need a same-origin Origin."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and token_ok(auth[7:].strip()):
        return "bearer"
    if _cookie_ok(request.cookies.get(COOKIE)):
        if request.method not in ("GET", "HEAD", "OPTIONS") and not origin_allowed(request.headers.get("origin"), request.headers.get("host")):
            raise HTTPException(403, "cross-origin request refused")
        return "cookie"
    raise HTTPException(401, "owner authentication required")


class LoginBody(BaseModel):
    token: str


@app.post("/api/login")
def login(body: LoginBody, request: Request, response: Response):
    key = request.client.host if request.client else "?"
    if throttle.blocked(key):
        raise HTTPException(429, "too many failed attempts; wait a few minutes")
    if not token_ok(body.token):
        throttle.record_failure(key)
        raise HTTPException(401, "wrong token")
    response.set_cookie(COOKIE, _make_cookie(), httponly=True, samesite="strict", max_age=SESSION_TTL,
                        secure=request.url.scheme == "https")
    return {"ok": True}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/state")
def state(_: str = Depends(owner)):
    s = get_settings()
    return {"cloud_enabled": s.cloud_enabled, "terminal_enabled": s.enable_terminal, "budget": budget.summary(),
            "llm_configured": bool((s.llm_base_url and s.llm_model) or (s.anthropic_api_key and s.llm_model)),
            "registry_verified_on": registry.verified_on()}


# ------------------------------------------------------------------ models / research / budget
@app.get("/api/models")
def models(_: str = Depends(owner)):
    return registry.all_models()


@app.get("/api/models/{model_id}/plan")
def model_plan(model_id: str, _: str = Depends(owner)):
    try:
        return installer.plan(model_id)
    except KeyError:
        raise HTTPException(404, "unknown model")


class InstallBody(BaseModel):
    override_noncommercial: bool = False


@app.post("/api/models/{model_id}/install")
def model_install(model_id: str, body: InstallBody | None = None, _: str = Depends(owner)):
    try:
        m = registry.get_model(model_id)
    except KeyError:
        raise HTTPException(404, "unknown model")
    if m["status"] == "candidate" and not m.get("adapter"):
        raise HTTPException(422, "no adapter exists for this model yet (candidate). Use the custom adapter workflow.")
    job = jobq.enqueue("install", None, {"model_id": model_id, "override_noncommercial": bool(body and body.override_noncommercial)},
                       paid=m["runs_on"] != "local")
    return job


@app.get("/api/research/search")
def research_search(q: str, source: str = "huggingface", _: str = Depends(owner)):
    try:
        r = Research()
        return r.search_github(q) if source == "github" else r.search_hf(q)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"search failed: {type(e).__name__}: {str(e)[:200]}")


@app.get("/api/research/inspect")
def research_inspect(url: str, _: str = Depends(owner)):
    try:
        return Research().inspect(url)
    except ValueError as e:
        raise HTTPException(422, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f"inspect failed: {type(e).__name__}: {str(e)[:200]}")


@app.post("/api/research/scaffold")
def research_scaffold(url: str, _: str = Depends(owner)):
    rep = Research().inspect(url)
    path = installer.scaffold_manifest(rep)
    return {"manifest": str(path.relative_to(ROOT)), "next": "Edit it, read the code you will run, then set review.reviewed_by_owner: true."}


@app.get("/api/budget")
def get_budget(_: str = Depends(owner)):
    return budget.summary()


@app.get("/api/estimate")
def get_estimate(duration_s: int, visuals: str = "procedural-draft", llm: str = "template-local",
                 tts: str = "kokoro-onnx-local", _: str = Depends(owner)):
    spec = PodcastSpec(duration_s=duration_s, visuals=visuals, llm=llm, tts=tts)
    return budget.estimate(duration_s, spec.uses_cloud())


# ------------------------------------------------------------------ projects
class SpecBody(BaseModel):
    instruction: str = ""
    spec: dict[str, Any] = {}


def _project_row(pid: str) -> dict[str, Any]:
    p = db.q1("SELECT * FROM projects WHERE id=?", (pid,))
    if not p:
        raise HTTPException(404, "no such project")
    return p


def _view_project(p: dict[str, Any]) -> dict[str, Any]:
    wd = get_settings().projects_dir / p["id"]
    ep = wd / "exports" / "episode.mp4"
    spec = json.loads(p["spec_json"])
    return {**{k: p[k] for k in ("id", "title", "instruction", "status", "created_at", "updated_at")}, "spec": spec,
            "has_video": ep.exists(), "video_mb": round(ep.stat().st_size / 1e6, 2) if ep.exists() else None,
            "jobs": [_view_job(j) for j in jobq.list_jobs(p["id"], 10)]}


def _view_job(j: dict[str, Any]) -> dict[str, Any]:
    out = {k: j[k] for k in ("id", "project_id", "kind", "status", "progress", "stage", "attempts", "max_attempts",
                             "est_cost_usd", "cost_cap_usd", "spent_usd", "paid", "error", "created_at", "started_at", "finished_at")}
    out["result"] = json.loads(j["result_json"]) if j.get("result_json") else None
    return out


@app.post("/api/projects")
def create_project(body: SpecBody, _: str = Depends(owner)):
    parsed = parse_instruction(body.instruction) if body.instruction else {"overrides": {}, "notes": []}
    try:
        spec = PodcastSpec(**{**parsed["overrides"], **body.spec})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"invalid spec: {e}")
    pid = db.new_id("proj")
    title = (body.instruction or spec.topic)[:60]
    db.x("INSERT INTO projects(id,title,instruction,spec_json,status,created_at,updated_at) VALUES(?,?,?,?, 'draft',?,?)",
         (pid, title, body.instruction, spec.model_dump_json(), db.now(), db.now()))
    (get_settings().projects_dir / pid).mkdir(parents=True, exist_ok=True)
    pf = preflight.check(spec)
    est = budget.estimate(spec.duration_s, spec.uses_cloud())
    return {"project": _view_project(_project_row(pid)), "notes": parsed["notes"], "preflight": pf, "estimate": est}


@app.get("/api/projects")
def list_projects(_: str = Depends(owner)):
    return [_view_project(p) for p in db.q("SELECT * FROM projects ORDER BY created_at DESC LIMIT 100")]


@app.get("/api/projects/{pid}")
def get_project(pid: str, _: str = Depends(owner)):
    p = _project_row(pid)
    spec = PodcastSpec(**json.loads(p["spec_json"]))
    return {"project": _view_project(p), "preflight": preflight.check(spec),
            "estimate": budget.estimate(spec.duration_s, spec.uses_cloud()), "checkpoints": ckpt.list_for(pid)}


class StartBody(BaseModel):
    spec: dict[str, Any] = {}


@app.post("/api/projects/{pid}/start")
def start_project(pid: str, body: StartBody | None = None, _: str = Depends(owner)):
    """Queue a run. Free jobs start immediately; paid jobs wait for explicit approval."""
    p = _project_row(pid)
    try:
        spec = PodcastSpec(**{**json.loads(p["spec_json"]), **((body.spec if body else None) or {})})
    except Exception as e:  # noqa: BLE001
        raise HTTPException(422, f"invalid spec: {e}")
    pf = preflight.check(spec)
    if pf["errors"]:
        raise HTTPException(422, {"message": "cannot start", "errors": pf["errors"]})
    active = [j for j in jobq.list_jobs(pid, 20) if j["status"] in ("queued", "running", "awaiting_approval")]
    if active:
        raise HTTPException(409, f"project already has an active job ({active[0]['id']})")
    est = budget.estimate(spec.duration_s, spec.uses_cloud())
    db.x("UPDATE projects SET spec_json=?,status='queued',updated_at=? WHERE id=?", (spec.model_dump_json(), db.now(), pid))
    job = jobq.enqueue("podcast", pid, json.loads(spec.model_dump_json()), paid=spec.is_paid(), est_cost_usd=est["typical_usd"])
    return {"job": _view_job(job), "estimate": est, "needs_approval": job["status"] == "awaiting_approval"}


@app.delete("/api/projects/{pid}")
def delete_project(pid: str, _: str = Depends(owner)):
    _project_row(pid)
    if any(j["status"] in ("queued", "running", "awaiting_approval") for j in jobq.list_jobs(pid, 50)):
        raise HTTPException(409, "cancel the active job first")
    shutil.rmtree(get_settings().projects_dir / pid, ignore_errors=True)
    for t in ("checkpoints", "chat"):
        db.x(f"DELETE FROM {t} WHERE project_id=?", (pid,))
    db.x("DELETE FROM job_events WHERE job_id IN (SELECT id FROM jobs WHERE project_id=?)", (pid,))
    db.x("DELETE FROM jobs WHERE project_id=?", (pid,))
    db.x("DELETE FROM projects WHERE id=?", (pid,))
    return {"deleted": pid}


MEDIA_OK = {"episode.mp4": "video/mp4", "captions.srt": "application/x-subrip", "script.json": "application/json",
            "timeline.json": "application/json", "episode.json": "application/json"}


@app.get("/api/projects/{pid}/files/{name}")
def project_file(pid: str, name: str, download: bool = False, _: str = Depends(owner)):
    if name not in MEDIA_OK:
        raise HTTPException(404, "not a downloadable file")
    _project_row(pid)
    base = get_settings().projects_dir / pid
    for sub in ("exports", "."):
        try:
            f = safe_join(base, f"{sub}/{name}")
        except ValueError:
            raise HTTPException(400, "bad path")
        if f.is_file():
            return FileResponse(f, media_type=MEDIA_OK[name], filename=name if download else None,
                                content_disposition_type="attachment" if download else "inline")
    raise HTTPException(404, "file not produced yet")


# ------------------------------------------------------------------ jobs
@app.get("/api/jobs")
def jobs(project_id: str | None = None, _: str = Depends(owner)):
    return [_view_job(j) for j in jobq.list_jobs(project_id, 50)]


def _job(job_id: str) -> dict[str, Any]:
    j = jobq.get(job_id)
    if not j:
        raise HTTPException(404, "no such job")
    return j


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str, _: str = Depends(owner)):
    return _view_job(_job(job_id))


@app.get("/api/jobs/{job_id}/events")
def job_events(job_id: str, after: int = 0, _: str = Depends(owner)):
    _job(job_id)
    return jobq.events(job_id, after)


@app.get("/api/jobs/{job_id}/stream")
async def job_stream(job_id: str, request: Request, _: str = Depends(owner)):
    _job(job_id)

    async def gen():
        last = 0
        while not await request.is_disconnected():
            for e in jobq.events(job_id, last):
                last = e["id"]
                yield f"data: {json.dumps(e)}\n\n"
            j = jobq.get(job_id)
            yield f"event: status\ndata: {json.dumps(_view_job(j))}\n\n"
            if j["status"] in jobq.TERMINAL:
                yield "event: done\ndata: {}\n\n"
                return
            await asyncio.sleep(1)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})


class ApproveBody(BaseModel):
    cost_cap_usd: float


@app.post("/api/jobs/{job_id}/approve")
def approve_job(job_id: str, body: ApproveBody, _: str = Depends(owner)):
    try:
        return _view_job(jobq.approve(job_id, body.cost_cap_usd))
    except budget.BudgetExceeded as e:
        raise HTTPException(422, str(e))
    except ValueError as e:
        raise HTTPException(409, str(e))
    except KeyError:
        raise HTTPException(404, "no such job")


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, _: str = Depends(owner)):
    try:
        return _view_job(jobq.cancel(job_id))
    except KeyError:
        raise HTTPException(404, "no such job")


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str, _: str = Depends(owner)):
    """Start a new run for the same project; finished checkpoints are reused automatically."""
    j = _job(job_id)
    if j["status"] not in ("failed", "cancelled"):
        raise HTTPException(409, "only failed or cancelled jobs can be retried")
    return start_project(j["project_id"], None)


# ------------------------------------------------------------------ chat
def _say(pid: str | None, role: str, text: str, data: Any = None) -> None:
    db.x("INSERT INTO chat(project_id,role,text,data_json,ts) VALUES(?,?,?,?,?)", (pid, role, text, db.dumps(data) if data else None, db.now()))


class ChatBody(BaseModel):
    text: str
    project_id: str | None = None


@app.get("/api/chat")
def chat_history(_: str = Depends(owner)):
    return [{**r, "data": json.loads(r["data_json"]) if r["data_json"] else None} for r in db.q("SELECT id,role,text,data_json,project_id,ts FROM chat ORDER BY id DESC LIMIT 60")][::-1]


@app.post("/api/chat")
def chat(body: ChatBody, _: str = Depends(owner)):
    """Deterministic command router (no LLM): search / inspect / install / podcast brief."""
    import re
    text = body.text.strip()
    _say(body.project_id, "user", text)
    low = text.lower()
    reply: dict[str, Any]
    m = re.match(r"^(?:search|find)\s+(github|hugging\s*face|hf)\s+(?:for\s+)?(.+)$", low)
    url = re.search(r"https?://\S+", text)
    if m:
        src = "github" if m.group(1) == "github" else "huggingface"
        try:
            r = Research()
            res = r.search_github(m.group(2)) if src == "github" else r.search_hf(m.group(2))
            reply = {"text": f"Top {len(res)} {src} results for '{m.group(2)}'. Say 'inspect <url>' to check licence, size and compatibility.", "results": res}
        except Exception as e:  # noqa: BLE001
            reply = {"text": f"Search failed: {type(e).__name__}: {str(e)[:160]}"}
    elif url and re.match(r"^(inspect|check|analy[sz]e)\b", low) or (url and len(text.split()) == 1):
        try:
            rep = Research().inspect(url.group(0).rstrip(".,)"))
            reply = {"text": f"Inspection of {rep['repo']}: licence {rep['license']['class']}, {rep['compatibility']['message']}", "inspection": rep}
        except Exception as e:  # noqa: BLE001
            reply = {"text": f"Could not inspect: {type(e).__name__}: {str(e)[:160]}"}
    elif m2 := re.match(r"^install\s+([\w.\-]+)$", low):
        mid = m2.group(1)
        try:
            reply = {"text": f"Install plan for {mid}. Use the Models tab to start it.", "plan": installer.plan(mid)}
        except KeyError:
            reply = {"text": f"Unknown model id '{mid}'. Open the Models tab for the list."}
    elif re.search(r"podcast|episode|show", low) or re.search(r"\d+\s*-?\s*(min|sec|hour)", low):
        parsed = parse_instruction(text)
        try:
            spec = PodcastSpec(**parsed["overrides"])
        except Exception as e:  # noqa: BLE001
            reply = {"text": f"I could not turn that into a valid plan: {e}"}
        else:
            made = create_project(SpecBody(instruction=text, spec=json.loads(spec.model_dump_json())))
            reply = {"text": "I drafted a project. Review the plan below, then start it. Nothing has run and nothing has been billed.",
                     "proposal": made}
    else:
        reply = {"text": "I understand: a podcast brief (e.g. 'Create a 30-second podcast with Matt and Chloe...'), "
                         "'search github <query>', 'search hugging face <query>', 'inspect <github or huggingface URL>', 'install <model-id>'."}
    _say(body.project_id, "assistant", reply["text"], {k: v for k, v in reply.items() if k != "text"})
    return reply


# ------------------------------------------------------------------ terminal
@app.websocket("/api/terminal")
async def ws_terminal(ws: WebSocket):
    s = get_settings()
    if not s.enable_terminal:
        await ws.close(code=4403)
        return
    if not _cookie_ok(ws.cookies.get(COOKIE)) or not origin_allowed(ws.headers.get("origin"), ws.headers.get("host")):
        await ws.close(code=4401)
        return
    await terminal.serve(ws)


# ------------------------------------------------------------------ static frontend (public shell, protected API)
app.mount("/", StaticFiles(directory=ROOT / "frontend", html=True), name="frontend")


def main() -> None:
    import uvicorn
    s = get_settings()
    uvicorn.run("app.main:app", host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    main()
