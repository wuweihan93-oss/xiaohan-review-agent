"""路由共用的依赖；身份只由匿名 Cookie 中间件生成。"""

from fastapi import Request


async def current_visitor(request: Request) -> str:
    return request.state.visitor_id
