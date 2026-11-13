"""历史会话接口；具体访问校验和删除流程交给服务层。"""

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import current_visitor
from app.services import conversations

router = APIRouter(prefix="/api/conversations", tags=["历史会话"])


@router.get("")
async def conversation_list(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    visitor_id: str = Depends(current_visitor),
):
    return await conversations.conversation_list(visitor_id, limit, offset)


@router.get("/{conversation_id}")
async def conversation_detail(
    conversation_id: str, visitor_id: str = Depends(current_visitor)
):
    return await conversations.conversation_detail(conversation_id, visitor_id)


@router.delete("/{conversation_id}")
async def remove_conversation(
    conversation_id: str, visitor_id: str = Depends(current_visitor)
):
    return await conversations.remove_conversation(conversation_id, visitor_id)
