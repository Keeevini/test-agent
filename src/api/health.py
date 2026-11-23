"""Health endpoint router."""

from fastapi import APIRouter


def create_health_router(llama_base_url, workspace, max_context_tokens) -> APIRouter:
    router = APIRouter()

    @router.get('/api/health')
    async def health():
        return {
            "ok": True,
            "llama_base_url": llama_base_url,
            "workspace": str(workspace),
            "max_context_tokens": max_context_tokens,
        }

    return router
