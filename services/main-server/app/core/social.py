"""소셜 OAuth(naver/google/kakao) provider 사용자 식별.

- dev 모드(provider client_id/secret 미설정): 외부 호출 없이 code 로부터 결정적 provider_user_id 를 만든다.
  동일 code → 동일 provider_user_id 이므로 연동→토큰 로그인 흐름을 로컬/CI 에서 검증할 수 있다.
- real 모드: provider 토큰 교환 → 프로필 조회로 provider_user_id 를 얻는다(지연 import httpx).

신규 기획 §4(소셜 연동) 참조. 소셜은 편의 로그인 수단이며 최초 가입은 본인인증으로만 한다.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from app.core.config import Settings

PROVIDERS = ("naver", "google", "kakao")


class SocialError(RuntimeError):
    pass


@dataclass
class SocialIdentity:
    provider: str
    provider_user_id: str
    access_token: str | None = None
    refresh_token: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def is_dev_mode(settings: Settings, provider: str) -> bool:
    conf = settings.social_providers.get(provider, {})
    return not (conf.get("client_id") and conf.get("client_secret"))


async def resolve_social_identity(
    settings: Settings,
    provider: str,
    code: str,
    redirect_uri: str | None = None,
) -> SocialIdentity:
    if provider not in PROVIDERS:
        raise SocialError(f"지원하지 않는 provider: {provider}")
    if is_dev_mode(settings, provider):
        digest = hashlib.sha256(f"{provider}:{code}".encode("utf-8")).hexdigest()
        return SocialIdentity(
            provider=provider,
            provider_user_id=f"{provider}_{digest[:24]}",
            access_token=f"dev-access-{digest[:16]}",
            refresh_token=f"dev-refresh-{digest[16:32]}",
            raw={"dev": True},
        )
    return await _resolve_real(settings, provider, code, redirect_uri)


async def _resolve_real(
    settings: Settings,
    provider: str,
    code: str,
    redirect_uri: str | None,
) -> SocialIdentity:  # pragma: no cover - 실 OAuth 자격증명 필요
    try:
        import httpx
    except ImportError as exc:
        raise SocialError("real 모드에는 httpx 가 필요합니다.") from exc

    conf = settings.social_providers[provider]
    token_url, profile_url = _endpoints(provider)
    async with httpx.AsyncClient(timeout=10.0) as client:
        token_resp = await client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "client_id": conf["client_id"],
                "client_secret": conf["client_secret"],
                "code": code,
                "redirect_uri": redirect_uri or "",
            },
            headers={"Accept": "application/json"},
        )
        token_body = token_resp.json()
        access_token = token_body.get("access_token")
        if not access_token:
            raise SocialError(f"{provider} 토큰 교환 실패")
        profile_resp = await client.get(
            profile_url, headers={"Authorization": f"Bearer {access_token}"}
        )
        profile = profile_resp.json()

    return SocialIdentity(
        provider=provider,
        provider_user_id=str(_extract_provider_user_id(provider, profile)),
        access_token=access_token,
        refresh_token=token_body.get("refresh_token"),
        raw=profile,
    )


def _endpoints(provider: str) -> tuple[str, str]:
    return {
        "naver": ("https://nid.naver.com/oauth2.0/token", "https://openapi.naver.com/v1/nid/me"),
        "google": (
            "https://oauth2.googleapis.com/token",
            "https://www.googleapis.com/oauth2/v2/userinfo",
        ),
        "kakao": ("https://kauth.kakao.com/oauth/token", "https://kapi.kakao.com/v2/user/me"),
    }[provider]


def _extract_provider_user_id(provider: str, profile: dict[str, Any]) -> Any:
    if provider == "naver":
        return profile.get("response", {}).get("id")
    if provider == "kakao":
        return profile.get("id")
    return profile.get("id")  # google
