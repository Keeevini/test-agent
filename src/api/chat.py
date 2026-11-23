"""Chat and approval SSE routers.

``create_chat_router(agent, approvals)`` builds the ``/api/chat`` and
``/api/approval`` routes with the agent and approval manager injected, so the
router owns no global state. SSE framing and response headers are unchanged
from the original ``app.py`` handlers.
"""

import json
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


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


def create_chat_router(agent, approvals) -> APIRouter:
    router = APIRouter()

    @router.post('/api/chat')
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

    @router.post('/api/approval')
    async def approval(request: ApprovalRequest):
        found = await approvals.resolve(request.approval_id, request.approved)
        if not found:
            raise HTTPException(status_code=404, detail="Approval request not found or already resolved")
        return {"ok": True}

    return router
