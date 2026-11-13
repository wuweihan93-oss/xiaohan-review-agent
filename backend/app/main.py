"""FastAPI 装配入口。启动命令仍为 uvicorn app.main:app。"""

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import admin, conversations, materials, review, system
from app.core.config import CORS_ORIGINS
from app.core.lifespan import lifespan
from app.repositories.conversations import ConversationNotFound
from app.security.client_info import TrustedProxyMiddleware
from app.security.headers import AdminHeadersMiddleware
from app.security.visitors import VisitorMiddleware


def create_app() -> FastAPI:
    """统一注册路由、中间件和中文错误处理。"""
    app = FastAPI(title="小晗带你复习", version="0.1.0", lifespan=lifespan)
    app.add_middleware(VisitorMiddleware)
    app.add_middleware(AdminHeadersMiddleware)
    app.add_middleware(TrustedProxyMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for module in (system, materials, conversations, review, admin):
        app.include_router(module.router)

    @app.exception_handler(ConversationNotFound)
    async def conversation_not_found(request, exc):
        return JSONResponse(status_code=404, content={"detail": "找不到这段复习对话"})

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request, exc):
        return JSONResponse(
            status_code=422, content={"detail": "请求内容有误，请检查输入后重试。"}
        )

    return app


app = create_app()
