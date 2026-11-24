from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.api.chat import create_chat_router
from src.api.health import create_health_router
from src.api.sessions import create_sessions_router
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

# Session history is project-owned state: only this app reads and writes it,
# never the model, so it lives in the per-machine ``local/`` tree rather than
# the model-facing ``vault/`` (which the agent's tools can touch).
LOCAL_DIR = BASE_DIR / "local"
SESSIONS_DIR = (LOCAL_DIR / "sessions").resolve()
DB_PATH = SESSIONS_DIR / "sessions.db"


def resolve_session_db() -> Path:
    """Return the sessions database path, creating the store if it is missing.

    A database that already exists in ``local/sessions`` is used as-is. When
    none is present the folder is created here and the persistent store
    initialises the database file and schema on first connect.
    """
    if not DB_PATH.exists():
        SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return DB_PATH


workspace_tools = WorkspaceTools(WORKSPACE)
approvals = ApprovalManager()
sessions = SessionStore(str(resolve_session_db()))
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


@app.get("/")
async def index():
    return FileResponse(SRC_DIR / "static" / "index.html")


app.include_router(create_health_router(LLAMA_BASE_URL, WORKSPACE, MAX_CONTEXT_TOKENS))
app.include_router(create_chat_router(agent, approvals))
app.include_router(create_sessions_router(
    sessions,
    max_context_tokens=MAX_CONTEXT_TOKENS,
    model=LLAMA_MODEL,
    tool_specs=workspace_tools.specs,
    tools=workspace_tools,
))
