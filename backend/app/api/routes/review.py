"""复习接口：JSON 便于调试，SSE 用于前端逐字展示。"""

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.dependencies import current_visitor
from app.schemas.review import ChatRequest
from app.security.client_info import client_ip
from app.services import review

router = APIRouter(prefix="/api/review", tags=["复习对话"])


@router.post("")
async def chat(
    request: ChatRequest,
    visitor_id: str = Depends(current_visitor),
    source_ip: str | None = Depends(client_ip),
):
    return await review.chat(request, visitor_id, source_ip)


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    visitor_id: str = Depends(current_visitor),
    source_ip: str | None = Depends(client_ip),
):
    events = await review.stream_events(request, visitor_id, source_ip)
    return StreamingResponse(
        events,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
