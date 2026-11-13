"""检索与复习接口的输入结构；与前端字段保持一致。"""

import uuid
from typing import Literal

from pydantic import BaseModel, Field

from app.core.config import RETRIEVAL_DEFAULT_RESULTS, RETRIEVAL_MAX_RESULTS


class SearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=1500)
    material_ids: list[str] = Field(default_factory=list)
    limit: int = Field(
        default=RETRIEVAL_DEFAULT_RESULTS, ge=1, le=RETRIEVAL_MAX_RESULTS
    )


class ChatRequest(BaseModel):
    question: str = Field(min_length=1, max_length=5000)
    conversation_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    material_ids: list[str] = Field(default_factory=list)
    study_mode: Literal["coach", "quiz"] = "coach"
    prompt: str | None = None
    web_search_enabled: bool = False
