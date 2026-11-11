from __future__ import annotations

import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from src.agent import ApprovalManager, LocalAgent, SessionStore
from src.agent.tools import WorkspaceTools

logger = logging.getLogger(__name__)

SRC_DIR = Path(__file__).resolve().parent
BASE_DIR = SRC_DIR.parent
WORKSPACE = Path(os.getenv("VAULT_ROOT", str(BASE_DIR / "vault"))).resolve()
# Docker Compose provides LLM_API_URL; the native launcher historically uses LLAMA_BASE_URL.
# Accept both, with LLAMA_BASE_URL taking precedence when explicitly set.
LLAMA_BASE_URL = (
    os.getenv("LLAMA_BASE_URL")
    or os.getenv("LLM_API_URL")
    or "http://127.0.0.1:8080/v1"
)
LLAMA_MODEL = os.getenv("LLAMA_MODEL", "local")
MAX_CONTEXT_TOKENS = int(os.getenv("MAX_CONTEXT_TOKENS", "16384"))

DB_PATH = WORKSPACE / "sessions.db"

workspace_tools = WorkspaceTools(WORKSPACE)
approvals = ApprovalManager()
sessions = SessionStore(str(DB_PATH))
agent = LocalAgent(
    LLAMA_BASE_URL,
    LLAMA_MODEL,
    workspace_tools,
    approvals,
    sessions,
    max_context_tokens=MAX_CONTEXT_TOKENS,
)


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger.info("Starting app; workspace=%s, db=%s", WORKSPACE, DB_PATH)
    yield
    sessions.close()
    await agent.close()


app = FastAPI(title="Local Agent UI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")


@app.middleware("http")
async def no_cache_static(request: Request, call_next):
    """Revalidate static assets on every load.

    This is a local dev server: the browser must always pick up the latest
    build of ``app.js`` / CSS / HTML, so we tell it to revalidate (via etag)
    rather than serve a heuristically-cached copy.
    """
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/static"):
        response.headers["Cache-Control"] = "no-cache"
    return response


class ChatRequest(BaseModel):
    session_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$",
    )
    message: str = Field(min_length=1, max_length=100_000)


class ApprovalRequest(BaseModel):
    approval_id: str
    approved: bool


class SessionRequest(BaseModel):
    session_id: str


@app.get("/")
async def index():
    return FileResponse(SRC_DIR / "static" / "index.html")


@app.get("/api/health")
async def health():
    return {
        "ok": True,
        "llama_base_url": LLAMA_BASE_URL,
        "workspace": str(WORKSPACE),
        "max_context_tokens": MAX_CONTEXT_TOKENS,
    }


@app.post("/api/chat")
async def chat(request: ChatRequest):
    async def event_stream():
        try:
            async for event in agent.stream_turn(request.session_id, request.message):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:
            logger.exception("Chat stream error for session %s", request.session_id)
            event = {"type": "error", "message": str(exc)}
            yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/approval")
async def approval(request: ApprovalRequest):
    found = await approvals.resolve(request.approval_id, request.approved)
    if not found:
        raise HTTPException(status_code=404, detail="Approval request not found or already resolved")
    return {"ok": True}


@app.post("/api/session/clear")
async def clear_session(request: SessionRequest):
    sessions.clear(request.session_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Session management endpoints
# ---------------------------------------------------------------------------

class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    title: str = "New Chat"


class UpdateSessionRequest(BaseModel):
    title: str


@app.get("/api/sessions")
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
):
    """List sessions ordered by most recently updated."""
    meta_list = sessions.list(limit=limit, offset=offset)
    return {
        "sessions": [
            {
                "session_id": m.session_id,
                "title": m.title,
                "model": m.model,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
                "tokens_total": m.tokens_total,
                "cost": m.cost,
                "tools_used": m.tools_used,
            }
            for m in meta_list
        ]
    }


@app.post("/api/sessions")
async def create_session(request: CreateSessionRequest):
    """Create a new session. If no session_id is provided, generate one."""
    session_id = request.session_id or str(uuid.uuid4())
    meta = sessions.create(session_id, title=request.title)
    return {"session_id": meta.session_id, "title": meta.title}


# NOTE: The ``/api/sessions/search`` route is declared before the
# ``/api/sessions/{session_id}`` route. FastAPI matches routes in declaration
# order, so the parameterized route would otherwise capture ``search`` as a
# session_id and the search endpoint would be unreachable.
@app.get("/api/sessions/search")
async def search_sessions(q: str, limit: int = 50):
    """Search sessions by title, session_id, or model."""
    meta_list = sessions.search(q, limit=limit)
    return {
        "sessions": [
            {
                "session_id": m.session_id,
                "title": m.title,
                "model": m.model,
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "tokens_in": m.tokens_in,
                "tokens_out": m.tokens_out,
                "tokens_total": m.tokens_total,
                "cost": m.cost,
                "tools_used": m.tools_used,
            }
            for m in meta_list
        ]
    }


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get session metadata."""
    meta = sessions.get(session_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": meta.session_id,
        "title": meta.title,
        "model": meta.model,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "tokens_in": meta.tokens_in,
        "tokens_out": meta.tokens_out,
        "tokens_total": meta.tokens_total,
        "cost": meta.cost,
        "tools_used": meta.tools_used,
    }


@app.put("/api/sessions/{session_id}")
async def update_session(session_id: str, request: UpdateSessionRequest):
    """Update session title."""
    sessions.update_title(session_id, request.title)
    return {"ok": True}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and its messages."""
    sessions.delete(session_id)
    return {"ok": True}


@app.get("/api/sessions/{session_id}/messages")
async def get_messages(session_id: str):
    """Get all messages for a session."""
    msgs = sessions.get_messages(session_id)
    return {"messages": msgs}
