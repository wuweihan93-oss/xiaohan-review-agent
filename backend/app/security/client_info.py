"""仅从明确可信的代理读取来源 IP，其他请求忽略可伪造的转发头。"""

import asyncio
import ipaddress
import socket
import time

from starlette.requests import Request

from app.core.config import TRUSTED_PROXY_CIDRS, TRUSTED_PROXY_HOSTS

_proxy_cache: dict[str, tuple[float, set[str]]] = {}


def _address(value: str | None) -> str | None:
    try:
        return str(ipaddress.ip_address(value or ""))
    except ValueError:
        return None


async def _proxy_addresses(host: str) -> set[str]:
    cached = _proxy_cache.get(host)
    if cached and cached[0] > time.monotonic():
        return cached[1]
    try:
        records = await asyncio.wait_for(
            asyncio.to_thread(socket.getaddrinfo, host, None, proto=socket.IPPROTO_TCP),
            timeout=2,
        )
        addresses = {
            address for record in records if (address := _address(record[4][0]))
        }
    except (OSError, asyncio.TimeoutError):
        # 无法确认可信代理时只记录连接来源，不盲目信任转发头。
        addresses = set()
    _proxy_cache[host] = (time.monotonic() + (30 if addresses else 2), addresses)
    return addresses


async def _trusted_proxy(peer: str) -> bool:
    trusted = False
    for cidr in TRUSTED_PROXY_CIDRS:
        try:
            if ipaddress.ip_address(peer) in ipaddress.ip_network(cidr, strict=False):
                trusted = True
                break
        except ValueError:
            continue
    if not trusted:
        for host in TRUSTED_PROXY_HOSTS:
            if peer in await _proxy_addresses(host):
                trusted = True
                break
    return trusted


async def client_ip(request: Request) -> str | None:
    if "source_ip" in request.scope.get("state", {}):
        return request.state.source_ip
    peer = _address(request.client.host if request.client else None)
    if peer is None:
        return None
    trusted = await _trusted_proxy(peer)
    # Nginx 用 $remote_addr 覆盖 X-Real-IP；不读取客户端自带的 X-Forwarded-For 链。
    if trusted:
        return _address(request.headers.get("x-real-ip")) or peer
    return peer


class TrustedProxyMiddleware:
    """统一校验代理来源后识别 IP 和协议，使 HTTPS Cookie 与来源校验一致。"""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        protected = path == "/api/visitor" or path.startswith(
            ("/api/admin", "/api/review", "/api/conversations")
        )
        if scope["type"] == "http" and protected:
            request = Request(scope)
            peer = _address(request.client.host if request.client else None)
            source = peer
            if peer and await _trusted_proxy(peer):
                source = _address(request.headers.get("x-real-ip")) or peer
                protocol = request.headers.get("x-forwarded-proto")
                if protocol in {"http", "https"}:
                    scope["scheme"] = protocol
            scope.setdefault("state", {})["source_ip"] = source
        await self.app(scope, receive, send)
