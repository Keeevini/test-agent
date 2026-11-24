"""Session management routers.

``create_sessions_router(sessions, ...)`` builds the session endpoints with
the ``SessionStore`` injected. ``serialize_session`` is the single projection
of a session metadata record onto its public API shape, shared by list/search/get.

Route ordering matters: ``/api/sessions/search`` is declared before
``/api/sessions/{session_id}`` so the parameterized route cannot capture
``search`` as a session_id.
"""

import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from src.agent.context import build_context_summary


class SessionRequest(BaseModel):
    session_id: str


class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    title: str = "Untitled Session"


class UpdateSessionRequest(BaseModel):
    title: str


def serialize_session(meta) -> dict:
    """Project a session metadata record onto its public API shape."""
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


def create_sessions_router(sessions, max_context_tokens: int = 16384,
                            model: str = "local", tool_specs: list | None = None, *,
                            tools=None) -> APIRouter:
    router = APIRouter()

    @router.post('/api/session/clear')
    async def clear_session(request: SessionRequest):
        sessions.clear(request.session_id)
        return {"ok": True}

    @router.get('/api/sessions')
    async def list_sessions(limit: int = 50, offset: int = 0):
        """List sessions ordered by most recently updated."""
        meta_list = sessions.list(limit=limit, offset=offset)
        return {"sessions": [serialize_session(m) for m in meta_list]}

    @router.post('/api/sessions')
    async def create_session(request: CreateSessionRequest):
        """Create a new session. If no session_id is provided, generate one."""
        session_id = request.session_id or str(uuid.uuid4())
        meta = sessions.create(session_id, title=request.title)
        return {"session_id": meta.session_id, "title": meta.title}

    # NOTE: The ``/api/sessions/search`` route is declared before the
    # ``/api/sessions/{session_id}`` route. FastAPI matches routes in
    # declaration order, so the parameterized route would otherwise capture
    # ``search`` as a session_id and the search endpoint would be unreachable.
    @router.get('/api/sessions/search')
    async def search_sessions(q: str, limit: int = 50):
        """Search sessions by title, session_id, or model."""
        meta_list = sessions.search(q, limit=limit)
        return {"sessions": [serialize_session(m) for m in meta_list]}

    @router.get('/api/sessions/{session_id}')
    async def get_session(session_id: str):
        """Get session metadata."""
        meta = sessions.get(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        return serialize_session(meta)

    @router.put('/api/sessions/{session_id}')
    async def update_session(session_id: str, request: UpdateSessionRequest):
        """Update session title."""
        sessions.update_title(session_id, request.title)
        return {"ok": True}

    @router.delete('/api/sessions/{session_id}')
    async def delete_session(session_id: str):
        """Delete a session and its messages."""
        sessions.delete(session_id)
        return {"ok": True}

    @router.get('/api/sessions/{session_id}/messages')
    async def get_messages(session_id: str):
        """Get all messages for a session."""
        msgs = sessions.get_messages(session_id)
        return {"messages": msgs, "tool_aliases": tools.aliases if tools is not None else {}}

    @router.get('/api/sessions/{session_id}/context')
    async def get_context(session_id: str):
        """Get read-only context analytics for a session."""
        meta = sessions.get(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        messages = sessions.get_messages(session_id)
        calls = sessions.list_model_calls(session_id)
        return build_context_summary(
            meta, messages, calls,
            limit=max_context_tokens, model=model, tool_specs=tool_specs or [],
        )

    @router.get('/api/sessions/{session_id}/context/messages')
    async def get_context_raw_messages(
        session_id: str,
        offset: int = Query(ge=0, default=0),
        limit: int = Query(ge=1, le=50, default=20),
    ):
        """Get a read-only paginated page of raw stored messages."""
        meta = sessions.get(session_id)
        if meta is None:
            raise HTTPException(status_code=404, detail="Session not found")
        page = sessions.get_raw_message_page(session_id, offset, limit)
        return {"session_id": session_id, **page}

    return router
