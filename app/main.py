from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import sys
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import store, vault

sys.path.insert(0, str(store.ROOT / "poc"))
from audit import load_dotenv
from personas import PERSONAS

load_dotenv(store.ROOT / ".env")
from .jobs import manager


@asynccontextmanager
async def lifespan(app):
    store.init()
    with store.connect() as db:
        db.execute("UPDATE audits SET status='failed', error='The server restarted during this audit. Check H Agent View for any remaining session before retrying.', finished=? WHERE status IN ('running','paused','stopping','queued')", (time.time(),))
        # Audits from before product scores existed: score them from their saved runs.
        from scoring import scorecard
        for row in db.execute("SELECT id, results, config FROM audits WHERE (scores IS NULL OR scores='{}') AND results!='[]'").fetchall():
            try:
                card = scorecard(json.loads(row["results"]), json.loads(row["config"]).get("personas"))
                db.execute("UPDATE audits SET scores=? WHERE id=?", (json.dumps(card), row["id"]))
            except Exception:
                pass
    yield
    await asyncio.to_thread(manager.shutdown)


app = FastAPI(title="Humble", lifespan=lifespan)
COOKIE = "humble_session"
LIVE_IMAGE = re.compile(r"live(-[0-9])?\.png")
FRAME_IMAGE = re.compile(r"[0-9]{3}\.jpg")
TERMINAL = {"completed", "failed", "stopped"}


@app.middleware("http")
async def local_requests(request, call_next):
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        allowed = {"http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:5173", "http://127.0.0.1:5173"}
        if origin and origin not in allowed:
            return Response("Origin not allowed", status_code=403)
        if request.headers.get("sec-fetch-site") == "cross-site":
            return Response("Cross-site request not allowed", status_code=403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    if request.url.path.startswith("/api") and "cache-control" not in response.headers:
        response.headers["Cache-Control"] = "no-store"
    return response


def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()


def password_hash(password, salt=None):
    salt = salt or secrets.token_hex(16)
    hashed = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=16384, r=8, p=1).hex()
    return salt + ":" + hashed


def current_user(request: Request):
    token = request.cookies.get(COOKIE, "")
    with store.connect() as db:
        user = db.execute("SELECT u.id,u.name,u.email FROM sessions s JOIN users u ON s.user_id=u.id WHERE s.token=? AND s.expires>?",
                          (token_hash(token), time.time())).fetchone()
    if not user:
        raise HTTPException(401, "Please sign in to continue.")
    return dict(user)


def create_session(response, user_id):
    token = secrets.token_urlsafe(32)
    with store.connect() as db:
        db.execute("DELETE FROM sessions WHERE expires<?", (time.time(),))
        db.execute("INSERT INTO sessions VALUES(?,?,?)", (token_hash(token), user_id, time.time() + 7 * 86400))
    response.set_cookie(COOKIE, token, httponly=True, samesite="strict", max_age=7 * 86400)


class AuthInput(BaseModel):
    name: str = Field(default="", max_length=80)
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def email_valid(cls, value):
        value = value.strip().lower()
        if "@" not in value or "." not in value.split("@")[-1]:
            raise ValueError("Enter a valid email address")
        return value


login_attempts: dict[str, list[float]] = {}


def throttle(request):
    host = request.client.host if request.client else "local"
    now = time.time()
    recent = [t for t in login_attempts.get(host, []) if now - t < 60]
    login_attempts[host] = recent + [now]
    if len(recent) >= 15:
        raise HTTPException(429, "Too many attempts. Please wait a minute.")


@app.post("/api/auth/register")
def register(body: AuthInput, response: Response, request: Request):
    throttle(request)
    name = body.name.strip()
    if not name:
        raise HTTPException(422, "Enter your name.")
    user_id = str(uuid.uuid4())
    with store.connect() as db:
        try:
            db.execute("INSERT INTO users VALUES(?,?,?,?,?)", (user_id, name, body.email, password_hash(body.password), time.time()))
        except sqlite3.IntegrityError:
            raise HTTPException(409, "An account with this email already exists. Sign in instead.")
    create_session(response, user_id)
    return {"id": user_id, "name": name, "email": body.email}


@app.post("/api/auth/login")
def login(body: AuthInput, response: Response, request: Request):
    throttle(request)
    with store.connect() as db:
        row = db.execute("SELECT * FROM users WHERE email=?", (body.email,)).fetchone()
    stored = row["password"] if row else password_hash("dummy-password")
    salt = stored.split(":")[0]
    if not hmac.compare_digest(password_hash(body.password, salt), stored) or not row:
        raise HTTPException(401, "Email or password is incorrect.")
    create_session(response, row["id"])
    return {k: row[k] for k in ("id", "name", "email")}


@app.post("/api/auth/logout")
def logout(request: Request, response: Response):
    with store.connect() as db:
        db.execute("DELETE FROM sessions WHERE token=?", (token_hash(request.cookies.get(COOKIE, "")),))
    response.delete_cookie(COOKIE)
    return {"ok": True}


@app.get("/api/auth/me")
def me(user=Depends(current_user)):
    return user


@app.get("/api/settings")
def settings(user=Depends(current_user)):
    from chrome import chrome_binary
    try:
        chrome_binary()
        chrome_ready = True
    except FileNotFoundError:
        chrome_ready = False
    return {"h_configured": bool(os.environ.get("HAI_API_KEY")), "chrome_ready": chrome_ready,
            "linkup_configured": bool(os.environ.get("LINKUP_API_KEY")),
            "devin_configured": bool(os.environ.get("DEVIN_API_KEY") and os.environ.get("DEVIN_ORG_ID")),
            "personas": [{"id": p.id, "label": p.label} for p in PERSONAS.values()]}


@app.get("/api/quota")
def quota(user=Depends(current_user)):
    if not os.environ.get("HAI_API_KEY"):
        raise HTTPException(503, "Add HAI_API_KEY to .env and restart the server.")
    from hai_agents import Client
    try:
        with Client() as client:
            return client.sessions.get_session_quota(request_options={"timeout_in_seconds": 10, "max_retries": 0}).model_dump(mode="json")
    except Exception:
        raise HTTPException(503, "Cannot reach H. Check your API key and network connection.")


class Journey(BaseModel):
    title: str = Field(min_length=1, max_length=100)
    goal: str = Field(min_length=10, max_length=2000)
    expect_answer_contains: str | None = Field(default=None, max_length=200)


class Credentials(BaseModel):
    username: str = Field(default="", max_length=254)
    password: str = Field(default="", max_length=1024)
    headers: dict[str, str] = Field(default_factory=dict, max_length=10)

    @field_validator("headers")
    @classmethod
    def valid_headers(cls, value):
        for key, val in value.items():
            if not key or len(key) > 100 or len(val) > 4096 or any(c in key + val for c in "\r\n"):
                raise ValueError("Invalid HTTP header")
        return value


class AuditInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=2000)
    tasks: list[Journey] = Field(min_length=1, max_length=5)
    personas: list[str] = Field(min_length=1, max_length=5)
    repetitions: int = Field(default=1, ge=1, le=5)
    credentials: Credentials = Field(default_factory=Credentials)
    vault_id: str | None = Field(default=None, max_length=36)
    # standard: audit, then suggested fixes with before/after images (re-evaluation is a separate, manual step)
    # auto: audit -> fix -> re-audit with the fixes, until every perspective passes or max_rounds
    mode: Literal["standard", "auto"] = "standard"
    max_rounds: int = Field(default=2, ge=1, le=4)

    @field_validator("url")
    @classmethod
    def valid_url(cls, value):
        parsed = urlsplit(value.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Use an http(s) URL without embedded credentials")
        return value.strip()

    @field_validator("personas")
    @classmethod
    def valid_personas(cls, value):
        if any(p not in PERSONAS for p in value):
            raise ValueError("Unknown persona")
        return list(dict.fromkeys(["baseline", *value]))


def owned_audit(audit_id, user):
    with store.connect() as db:
        row = db.execute("SELECT * FROM audits WHERE id=? AND user_id=?", (audit_id, user["id"])).fetchone()
    if not row:
        raise HTTPException(404, "Audit not found.")
    return store.serialize(row)


class VaultInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=8, max_length=2000)
    credentials: Credentials

    @field_validator("name")
    @classmethod
    def trimmed_name(cls, value):
        if not value.strip():
            raise ValueError("Enter a credential name")
        return value.strip()


def vault_payload(body):
    data = body.credentials.model_dump()
    if bool(data["username"]) != bool(data["password"]):
        raise HTTPException(422, "Provide both username and password for a login.")
    if not data["username"] and not data["headers"]:
        raise HTTPException(422, "Add a login or at least one access header.")
    return data


@app.get("/api/vault")
def list_vault(user=Depends(current_user)):
    with store.connect() as db:
        rows = db.execute("SELECT * FROM vault_entries WHERE user_id=? ORDER BY updated DESC", (user["id"],)).fetchall()
    return [vault.metadata(row) for row in rows]


@app.post("/api/vault", status_code=201)
def create_vault(body: VaultInput, user=Depends(current_user)):
    data = vault_payload(body)
    site_origin = vault.origin(body.url)
    entry_id, now = str(uuid.uuid4()), time.time()
    encrypted = vault.encrypt(user["id"], data)
    with store.connect() as db:
        db.execute("INSERT INTO vault_entries VALUES(?,?,?,?,?,?,?,?,?)", (entry_id, user["id"], body.name,
                   site_origin, encrypted, bool(data["username"]), len(data["headers"]), now, now))
    return vault.metadata(vault.owned(entry_id, user["id"]))


@app.put("/api/vault/{entry_id}")
def replace_vault(entry_id: str, body: VaultInput, user=Depends(current_user)):
    vault.owned(entry_id, user["id"])
    data = vault_payload(body)
    encrypted = vault.encrypt(user["id"], data)
    with store.connect() as db:
        db.execute("UPDATE vault_entries SET name=?,origin=?,secret=?,has_login=?,header_count=?,updated=? WHERE id=? AND user_id=?",
                   (body.name, vault.origin(body.url), encrypted, bool(data["username"]), len(data["headers"]), time.time(), entry_id, user["id"]))
    return vault.metadata(vault.owned(entry_id, user["id"]))


@app.delete("/api/vault/{entry_id}")
def delete_vault(entry_id: str, user=Depends(current_user)):
    vault.owned(entry_id, user["id"])
    with store.connect() as db:
        db.execute("DELETE FROM vault_entries WHERE id=? AND user_id=?", (entry_id, user["id"]))
    return {"ok": True}


@app.get("/api/projects")
def projects(user=Depends(current_user)):
    with store.connect() as db:
        rows = db.execute("SELECT p.*, COUNT(a.id) AS audit_count, MAX(a.created) AS last_audit FROM projects p LEFT JOIN audits a ON a.project_id=p.id WHERE p.user_id=? GROUP BY p.id ORDER BY p.created DESC", (user["id"],)).fetchall()
    return [store.serialize(row) for row in rows]


class ResearchInput(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


@app.post("/api/research/journeys")
def research_journeys(body: ResearchInput, user=Depends(current_user)):
    """Linkup: what the site is, the journeys its visitors come for, and its declared accessibility."""
    import research
    if not research.available():
        raise HTTPException(503, "Add LINKUP_API_KEY to .env and restart the server to get journey suggestions.")
    if not research.is_public(body.url):
        raise HTTPException(422, "Linkup searches the public web: it cannot see local or private sites. Write the journeys yourself.")
    try:
        result = research.suggest_journeys(body.url.strip())
    except Exception:
        raise HTTPException(502, "Linkup did not answer. Try again, or write the journeys yourself.")
    if not result or not result.get("journeys"):
        raise HTTPException(404, "Linkup found nothing about this website. Write the journeys yourself.")
    return result


@app.get("/api/audits")
def audits(user=Depends(current_user)):
    with store.connect() as db:
        rows = db.execute("SELECT id,project_id,name,url,status,created,config,summary,progress,error,finished,scores,parent_id FROM audits WHERE user_id=? ORDER BY created DESC", (user["id"],)).fetchall()
    return [store.serialize(row) for row in rows]


@app.post("/api/audits", status_code=201)
def create_audit(body: AuditInput, user=Depends(current_user)):
    return launch_audit(body, user)


def launch_audit(body: AuditInput, user, extra: dict | None = None, parent_id: str | None = None):
    credentials = body.credentials
    vault_name = None
    if body.vault_id:
        if credentials.username or credentials.password or credentials.headers:
            raise HTTPException(422, "Choose a vault entry or manual credentials, not both.")
        entry = vault.owned(body.vault_id, user["id"])
        if vault.origin(body.url) != entry["origin"]:
            raise HTTPException(422, "This vault entry belongs to a different website origin. Choose a matching entry.")
        credentials = Credentials.model_validate(vault.decrypt(user["id"], entry))
        vault_name = entry["name"]
    if not os.environ.get("HAI_API_KEY"):
        raise HTTPException(503, "Add HAI_API_KEY to .env and restart the server.")
    from chrome import chrome_binary
    try:
        chrome_binary()
    except FileNotFoundError:
        raise HTTPException(503, "Chrome was not found. Install Google Chrome or set AUDIT_CHROME in .env, then restart.")
    if manager.current is not None:
        raise HTTPException(409, "Another audit is active on this local server. Wait for it to finish before starting a new one.")
    audit_id, project_id = str(uuid.uuid4()), str(uuid.uuid4())
    config = body.model_dump(exclude={"credentials"})
    # Whether access was needed (never the values): a re-evaluation must ask for manual credentials again.
    config["uses_login"] = bool(credentials.username or credentials.password)
    config["uses_headers"] = bool(credentials.headers)
    config.update(extra or {})
    if vault_name:
        config["vault_name"] = vault_name
    for index, task in enumerate(config["tasks"]):
        task.update(id=f"journey-{index + 1}", max_steps=35, max_time_s=360)
    host = urlsplit(body.url).netloc
    with store.connect() as db:
        row = db.execute("SELECT id FROM projects WHERE user_id=? AND url=?", (user["id"], body.url)).fetchone()
        if row:
            project_id = row["id"]
        else:
            db.execute("INSERT INTO projects VALUES(?,?,?,?,?)", (project_id, user["id"], host, body.url, time.time()))
        db.execute("INSERT INTO audits(id,user_id,project_id,name,url,status,created,config,parent_id) VALUES(?,?,?,?,?,?,?,?,?)",
                   (audit_id, user["id"], project_id, body.name, body.url, "queued", time.time(), json.dumps(config), parent_id))
    auth = {"headers": credentials.headers}
    if credentials.username or credentials.password:
        auth["login"] = {"username": credentials.username, "password": credentials.password}
    try:
        started = manager.start(audit_id, config, auth)
    except Exception:
        store.update(audit_id, status="failed", error="Unable to start the audit worker.", finished=time.time())
        raise HTTPException(500, "Unable to start the audit worker.")
    if not started:
        store.update(audit_id, status="failed", error="Another audit started first. Please try again when it finishes.", finished=time.time())
        raise HTTPException(409, "Another audit is active. Please wait for it to finish.")
    return owned_audit(audit_id, user)


@app.get("/api/audits/{audit_id}")
def audit_detail(audit_id: str, user=Depends(current_user)):
    audit = owned_audit(audit_id, user)
    if not audit.get("scores") and audit.get("results"):
        from scoring import scorecard  # audits saved without scores (older ones, fixtures)
        audit["scores"] = scorecard(audit["results"], audit["config"].get("personas"))
    with store.connect() as db:
        rows = db.execute("SELECT id,created,kind,message FROM events WHERE audit_id=? ORDER BY id DESC LIMIT 100", (audit_id,)).fetchall()
        audit["events"] = [dict(row) for row in reversed(rows)]
        # Re-evaluation links: a child audit re-runs its parent's journeys with the parent's suggested fixes.
        children = db.execute("SELECT id,name,status,created,summary,scores FROM audits WHERE parent_id=? AND user_id=? "
                              "ORDER BY created DESC", (audit_id, user["id"])).fetchall()
        audit["reevaluations"] = [store.serialize(row) for row in children]
        if audit.get("parent_id"):
            parent = db.execute("SELECT id,name,summary,fixes,scores,results,config FROM audits WHERE id=? AND user_id=?",
                                (audit["parent_id"], user["id"])).fetchone()
            audit["parent"] = store.serialize(parent) if parent else None
            if audit["parent"]:
                par = audit["parent"]
                if not par.get("scores") and par.get("results"):
                    from scoring import scorecard
                    par["scores"] = scorecard(par["results"], par["config"].get("personas"))
                par.pop("results", None)
                par.pop("config", None)
    return audit


class ReevaluateInput(BaseModel):
    credentials: Credentials = Field(default_factory=Credentials)


@app.post("/api/audits/{audit_id}/reevaluate", status_code=201)
def reevaluate(audit_id: str, body: ReevaluateInput, user=Depends(current_user)):
    """Run the same journeys and perspectives again, with the suggested fixes injected in every page.
    Manual on purpose: it re-runs every perspective. The new audit links back to this one for before/after."""
    parent = owned_audit(audit_id, user)
    patch = (parent.get("fixes") or {}).get("patch") or {}
    if parent["status"] not in TERMINAL:
        raise HTTPException(409, "Wait for this audit to finish before re-evaluating it.")
    if not patch.get("fixes"):
        raise HTTPException(409, "This audit has no suggested fixes to evaluate.")
    cfg = parent["config"]
    if not cfg.get("vault_id"):
        if cfg.get("uses_login") and not (body.credentials.username or body.credentials.password):
            raise HTTPException(422, "Enter the website login again: manual credentials are never stored.")
        if cfg.get("uses_headers") and not body.credentials.headers:
            raise HTTPException(422, "Enter the access headers again: manual headers are never stored.")
    again = AuditInput(name=f"{parent['name']} · with fixes"[:100], url=parent["url"], tasks=cfg["tasks"],
                       personas=cfg["personas"], repetitions=cfg["repetitions"], vault_id=cfg.get("vault_id"),
                       credentials=body.credentials if not cfg.get("vault_id") else Credentials())
    return launch_audit(again, user, extra={"patch": patch, "mode": "standard"}, parent_id=audit_id)


@app.get("/api/audits/{audit_id}/fixes/{filename}")
def fix_image(audit_id: str, filename: str, user=Depends(current_user)):
    audit = owned_audit(audit_id, user)
    allowed = {name for c in (audit.get("fixes") or {}).get("comparisons", []) for name in (c.get("before"), c.get("after"))}
    path = store.DATA / "audits" / audit_id / "fixes" / filename
    if filename not in allowed or Path(filename).name != filename or not filename.endswith(".png") or not path.is_file():
        raise HTTPException(404, "Image not found.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/audits/{audit_id}/events")
async def audit_events(audit_id: str, request: Request, user=Depends(current_user)):
    owned_audit(audit_id, user)

    async def stream():
        while not await request.is_disconnected():
            audit = await asyncio.to_thread(audit_detail, audit_id, user)
            yield f"data: {json.dumps(audit)}\n\n"
            if audit["status"] in TERMINAL:
                break
            await asyncio.sleep(2)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"X-Accel-Buffering": "no"})


@app.post("/api/audits/{audit_id}/{action}")
def control(audit_id: str, action: Literal["pause", "resume", "stop"], user=Depends(current_user)):
    audit = owned_audit(audit_id, user)
    if audit["status"] in TERMINAL or not manager.control(audit_id, action):
        raise HTTPException(409, "This audit is no longer active.")
    return owned_audit(audit_id, user)


@app.delete("/api/audits/{audit_id}")
def delete_audit(audit_id: str, user=Depends(current_user)):
    """Delete a finished audit, its events and its files (screenshots, replays, snapshots). Re-evaluations of it stay."""
    import shutil
    audit = owned_audit(audit_id, user)
    if audit["status"] not in TERMINAL:
        raise HTTPException(409, "Stop this audit before deleting it.")
    with store.connect() as db:
        db.execute("UPDATE audits SET parent_id=NULL WHERE parent_id=? AND user_id=?", (audit_id, user["id"]))
        db.execute("DELETE FROM events WHERE audit_id=?", (audit_id,))
        db.execute("DELETE FROM audits WHERE id=? AND user_id=?", (audit_id, user["id"]))
    folder = store.DATA / "audits" / audit_id
    if folder.is_dir() and folder.parent == store.DATA / "audits":
        shutil.rmtree(folder, ignore_errors=True)
    return {"ok": True}


@app.get("/api/audits/{audit_id}/export")
def export(audit_id: str, user=Depends(current_user)):
    data = audit_detail(audit_id, user)
    return Response(json.dumps(data, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="humble-audit-{audit_id[:8]}.json"'})


class SteerInput(BaseModel):
    message: str = Field(min_length=2, max_length=500)


@app.post("/api/audits/{audit_id}/lanes/{lane_id}/steer")
def steer(audit_id: str, lane_id: str, body: SteerInput, user=Depends(current_user)):
    """Send a message to one running agent (H steering). The run is then marked as assisted."""
    audit = owned_audit(audit_id, user)
    lane = ((audit.get("progress") or {}).get("lanes") or {}).get(lane_id) or {}
    if audit["status"] in TERMINAL or lane.get("state") != "running" or not lane.get("session_id"):
        raise HTTPException(409, "This agent is not running.")
    from hai_agents import Client
    from hai_agents.sessions.types.send_session_messages_request_body import SendSessionMessagesRequestBody_UserMessage
    try:
        with Client() as client:
            client.sessions.send_session_messages(
                lane["session_id"], request=SendSessionMessagesRequestBody_UserMessage(message=body.message.strip()),
                request_options={"timeout_in_seconds": 15, "max_retries": 1})
    except Exception:
        raise HTTPException(502, "H did not accept the message. Try again.")
    manager.assisted.add(lane["session_id"])
    persona = (lane.get("persona") or "").replace("_", " ").capitalize()
    store.event(audit_id, "steer", f"{persona}: you sent “{body.message.strip()[:200]}”. This run is now marked as assisted.")
    return {"ok": True}


@app.get("/api/audits/{audit_id}/report.html")
def export_html(audit_id: str, user=Depends(current_user)):
    """A self-contained HTML report (images and replays embedded) that opens anywhere, offline."""
    from export_report import build
    audit = owned_audit(audit_id, user)
    with store.connect() as db:
        child = db.execute("SELECT scores FROM audits WHERE parent_id=? AND user_id=? AND status='completed' "
                           "ORDER BY created DESC LIMIT 1", (audit_id, user["id"])).fetchone()
    after = json.loads(child["scores"]) if child and child["scores"] else None
    page = build(audit, store.DATA / "audits" / audit_id, after=after)
    return Response(page, media_type="text/html; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="humble-report-{audit_id[:8]}.html"'})


@app.get("/api/audits/{audit_id}/files/{filename}")
def artifact(audit_id: str, filename: str, user=Depends(current_user)):
    audit = owned_audit(audit_id, user)
    allowed = {r.get("final_screenshot") for r in audit["results"]}
    if not LIVE_IMAGE.fullmatch(filename) and filename not in allowed:
        raise HTTPException(404, "Image not found.")
    path = store.DATA / "audits" / audit_id / filename
    if Path(filename).name != filename or not filename.endswith(".png") or not path.is_file():
        raise HTTPException(404, "Image not found.")
    return FileResponse(path, media_type="image/png")


@app.get("/api/audits/{audit_id}/frames/{tag}/{filename}")
def frame(audit_id: str, tag: str, filename: str, user=Depends(current_user)):
    """One replay frame: the screenshot an agent decided a step on."""
    audit = owned_audit(audit_id, user)
    tags = {r.get("tag") for r in audit["results"]}
    if tag not in tags or not FRAME_IMAGE.fullmatch(filename):
        raise HTTPException(404, "Frame not found.")
    path = store.DATA / "audits" / audit_id / "frames" / tag / filename
    if not path.is_file():
        raise HTTPException(404, "Frame not found.")
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=86400"})


@app.get("/api/health")
def health():
    return {"status": "ok"}


DIST = store.ROOT / "dist"
if (DIST / "assets").exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")


@app.get("/{path:path}")
def frontend(path: str):
    if path.startswith("api/"):
        raise HTTPException(404, "Not found")
    if not (DIST / "index.html").exists():
        return Response("Frontend not built. Run npm run build, or use http://127.0.0.1:5173 with npm run dev.", status_code=503)
    return FileResponse(DIST / "index.html")
