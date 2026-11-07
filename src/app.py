from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
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

workspace_tools = WorkspaceTools(WORKSPACE)
approvals = ApprovalManager()
sessions = SessionStore()
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
    logger.info("Starting app; workspace=%s", WORKSPACE)
    yield
    await agent.close()


app = FastAPI(title="Local Agent UI", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=SRC_DIR / "static"), name="static")


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
