from __future__ import annotations

import time
from collections import deque
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field, field_validator

from r2.brain import ledger_path
from r2.privacy import EgressLedger
from r2.runtime import Runtime, TaskBusy
from r2.skills import SkillError, SkillStore
from r2.tools import screen

WEB_DIR = Path(__file__).parent / "web"

_CSP = (
    "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' blob:; "
    "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
)


class PairBody(BaseModel):
    code: str = Field(min_length=1, max_length=64)


class TaskBody(BaseModel):
    message: str = Field(min_length=1, max_length=4000)

    @field_validator("message")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("message must not be blank")
        return value.strip()


class ApproveBody(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    approve: bool


def create_app(rt: Runtime) -> FastAPI:
    app = FastAPI(title="R2", docs_url=None, redoc_url=None, openapi_url=None)
    bearer = HTTPBearer(auto_error=False)

    def require_token(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:
        if creds is None or not rt.auth.verify(creds.credentials):
            raise HTTPException(status_code=401, detail="invalid or missing token")

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    # ---- static page ----
    @app.get("/", response_class=HTMLResponse)
    def index():
        return (WEB_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/favicon.ico")
    def favicon():
        return Response(status_code=204)

    @app.get("/app.js")
    def app_js():
        return Response((WEB_DIR / "app.js").read_text(encoding="utf-8"), media_type="text/javascript")

    @app.get("/app.css")
    def app_css():
        return Response((WEB_DIR / "app.css").read_text(encoding="utf-8"), media_type="text/css")

    # ---- pairing (the only unauthenticated API) ----
    @app.post("/api/pair")
    def pair(body: PairBody):
        token = rt.auth.redeem(body.code)
        if token is None:
            raise HTTPException(status_code=401, detail="invalid or expired code")
        return {"token": token}

    # ---- authenticated API ----
    api = [Depends(require_token)]
    started: deque[float] = deque()  # demo profile only: when recent tasks began

    def demo_rate_limit() -> None:
        if rt.settings.profile != "demo":
            return
        now = time.time()
        while started and now - started[0] > 3600:
            started.popleft()
        if len(started) >= rt.settings.demo_tasks_per_hour:
            raise HTTPException(status_code=429, detail="The public demo is rate limited; please try again later.")
        started.append(now)

    @app.get("/api/status", dependencies=api)
    def status():
        return {**rt.tasks.status(), "demo": rt.settings.profile == "demo"}

    @app.post("/api/tasks", status_code=202, dependencies=api)
    def start_task(body: TaskBody):
        demo_rate_limit()
        try:
            return {"task_id": rt.tasks.start(body.message)}
        except TaskBusy as exc:
            raise HTTPException(status_code=409, detail=str(exc))

    @app.post("/api/approve", dependencies=api)
    def approve(body: ApproveBody):
        if not rt.broker.resolve(body.id, body.approve):
            raise HTTPException(status_code=404, detail="no such pending approval (it may have expired)")
        return {"ok": True}

    @app.post("/api/stop", dependencies=api)
    def stop():
        rt.tasks.stop()
        return {"ok": True}

    @app.get("/api/screenshot", dependencies=api)
    def screenshot():
        if rt.settings.profile == "demo":  # the public demo must never expose the host's screen
            raise HTTPException(status_code=403, detail="not available in the public demo")
        path = Path(rt.settings.data_dir) / "screens" / "latest.png"
        try:
            screen.screenshot(str(path))
        except Exception as exc:
            raise HTTPException(status_code=503, detail=f"cannot capture the screen: {exc}")
        return FileResponse(path, media_type="image/png")

    @app.get("/api/facts", dependencies=api)
    def facts():
        return [{"id": f.id, "text": f.text, "source": f.source, "ts": f.ts} for f in rt.memory.list_facts()]

    @app.delete("/api/facts/{fact_id}", dependencies=api)
    def delete_fact(fact_id: int):
        if not rt.memory.delete_fact(fact_id):
            raise HTTPException(status_code=404, detail="no such fact")
        return {"ok": True}

    @app.get("/api/actions", dependencies=api)
    def actions():
        return rt.memory.list_actions(50)

    @app.get("/api/chat", dependencies=api)
    def chat():
        return rt.memory.recent_messages(40)

    @app.delete("/api/history", dependencies=api)
    def clear_history():
        rt.memory.clear_history()
        return {"ok": True}

    @app.get("/api/ledger", dependencies=api)
    def ledger():
        led = EgressLedger(ledger_path(rt.settings))
        rows = led.read(200)
        routes = [r for r in rows if "route" in r]
        return {
            "summary": led.summary(),
            "stayed_local": sum(1 for r in routes if not r.get("left_pc")),
            "routed": len(routes),
            "recent": rows[-12:][::-1],
        }

    @app.get("/api/skills", dependencies=api)
    def skills():
        store = SkillStore(rt.settings.data_dir)
        pack = lambda s: {"name": s.name, "description": s.description, "source": s.source, "body": s.body}
        return {"skills": [pack(s) for s in store.all()], "proposed": [pack(s) for s in store.proposed()]}

    @app.post("/api/skills/{name}/approve", dependencies=api)
    def approve_skill(name: str):
        try:
            SkillStore(rt.settings.data_dir).approve(name)
        except SkillError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        rt.memory.log_action("approve_skill", {"name": name}, "approved by you", "user")
        return {"ok": True}

    @app.delete("/api/skills/{name}/proposed", dependencies=api)
    def reject_skill(name: str):
        if not SkillStore(rt.settings.data_dir).reject(name):
            raise HTTPException(status_code=404, detail="no such proposal")
        return {"ok": True}

    @app.post("/api/revoke", dependencies=api)
    def revoke():
        if rt.settings.profile == "demo":  # one visitor must not be able to sign everyone else out
            raise HTTPException(status_code=403, detail="not available in the public demo")
        rt.auth.revoke_all()
        return {"ok": True}

    return app
