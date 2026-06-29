"""응답 보안 헤더 미들웨어 (Spring Security 기본 헤더에 대응).

JSON API 경계이므로 자원 로딩을 막는 강한 CSP(default-src) 대신, API 에 의미 있는
clickjacking 방지(frame-ancestors/X-Frame-Options)와 MIME 스니핑/Referrer/Permissions 를 건다.
풀 CSP(스크립트/스타일 출처 제한)는 실제 HTML 을 내려주는 Next.js 프론트(web)에서 설정한다.

- X-Content-Type-Options: nosniff
- X-Frame-Options: DENY  +  CSP frame-ancestors 'none'   (clickjacking)
- Referrer-Policy: no-referrer
- Permissions-Policy: 위치/마이크/카메라 차단
- Cross-Origin-Opener-Policy: same-origin
- Strict-Transport-Security: HTTPS(prod)에서만
"""
from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_BASE_HEADERS: tuple[tuple[bytes, bytes], ...] = (
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"content-security-policy", b"frame-ancestors 'none'"),
    (b"referrer-policy", b"no-referrer"),
    (b"permissions-policy", b"geolocation=(), microphone=(), camera=()"),
    (b"cross-origin-opener-policy", b"same-origin"),
)
_HSTS = (b"strict-transport-security", b"max-age=63072000; includeSubDomains")


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool = False) -> None:
        self.app = app
        self.extra = (*_BASE_HEADERS, _HSTS) if hsts else _BASE_HEADERS

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                present = {name.lower() for name, _ in headers}
                for name, value in self.extra:
                    if name not in present:
                        headers.append((name, value))
            await send(message)

        await self.app(scope, receive, send_wrapper)
