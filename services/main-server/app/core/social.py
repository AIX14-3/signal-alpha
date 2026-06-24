"""소셜 OAuth(naver/google/kakao) provider 사용자 식별.

- dev 모드(provider client_id/secret 미설정): 외부 호출 없이 code 로부터 결정적 provider_user_id 를 만든다.
  동일 code → 동일 provider_user_id 이므로 연동→토큰 로그인 흐름을 로컬/CI 에서 검증할 수 있다.
- real 모드: provider 별 토큰 교환 → 고유 id(naver response.id / kakao id / google id_token.sub)만 추출.
  이메일·전화·프로필 등 개인정보는 사용하지 않는다. httpx 는 지연 import.

신규 기획 §4(소셜 연동) 참조. 소셜은 편의 로그인 수단이며 최초 가입은 본인인증으로만 한다.
"""
from __future__ import annotations

import base64
import hashlib
import json
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
    state: str | None = None,
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
    return await _resolve_real(settings, provider, code, redirect_uri, state)


async def _resolve_real(
    settings: Settings,
    provider: str,
    code: str,
    redirect_uri: str | None,
    state: str | None,
) -> SocialIdentity:  # pragma: no cover - 실 OAuth 자격증명 필요
    httpx = _import_httpx()
    conf = settings.social_providers[provider]
    async with httpx.AsyncClient(timeout=10.0) as client:
        if provider == "naver":
            token = (
                await client.post(
                    "https://nid.naver.com/oauth2.0/token",
                    params={
                        "grant_type": "authorization_code",
                        "client_id": conf["client_id"],
                        "client_secret": conf["client_secret"],
                        "code": code,
                        "state": state or "",
                    },
                )
            ).json()
            access = token.get("access_token")
            if not access:
                raise SocialError(f"naver 토큰 교환 실패: {token.get('error_description') or token.get('error')}")
            profile = (
                await client.get(
                    "https://openapi.naver.com/v1/nid/me",
                    headers={"Authorization": f"Bearer {access}"},
                )
            ).json()
            uid = (profile.get("response") or {}).get("id")
        elif provider == "kakao":
            data = {
                "grant_type": "authorization_code",
                "client_id": conf["client_id"],
                "redirect_uri": redirect_uri or "",
                "code": code,
            }
            if conf.get("client_secret"):
                data["client_secret"] = conf["client_secret"]
            token = (await client.post("https://kauth.kakao.com/oauth/token", data=data)).json()
            access = token.get("access_token")
            if not access:
                raise SocialError(f"kakao 토큰 교환 실패: {token.get('error_description') or token.get('error')}")
            profile = (
                await client.get(
                    "https://kapi.kakao.com/v2/user/me",
                    headers={"Authorization": f"Bearer {access}"},
                )
            ).json()
            uid = profile.get("id")
        else:  # google
            token = (
                await client.post(
                    "https://oauth2.googleapis.com/token",
                    data={
                        "grant_type": "authorization_code",
                        "client_id": conf["client_id"],
                        "client_secret": conf["client_secret"],
                        "code": code,
                        "redirect_uri": redirect_uri or "",
                    },
                )
            ).json()
            access = token.get("access_token")
            if not access and not token.get("id_token"):
                raise SocialError(f"google 토큰 교환 실패: {token.get('error_description') or token.get('error')}")
            uid = _google_sub(token.get("id_token"))
            if not uid and access:
                profile = (
                    await client.get(
                        "https://www.googleapis.com/oauth2/v2/userinfo",
                        headers={"Authorization": f"Bearer {access}"},
                    )
                ).json()
                uid = profile.get("id")
            profile = token

    if not uid:
        raise SocialError(f"{provider} 사용자 식별 실패")
    return SocialIdentity(
        provider=provider,
        provider_user_id=str(uid),
        access_token=token.get("access_token"),
        refresh_token=token.get("refresh_token"),
        raw=profile if isinstance(profile, dict) else {},
    )


def _google_sub(id_token: str | None) -> str | None:
    if not id_token:
        return None
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        sub = data.get("sub")
        return str(sub) if sub else None
    except (ValueError, IndexError, KeyError):
        return None


def _import_httpx() -> Any:
    try:
        import httpx
    except ImportError as exc:
        raise SocialError("real 모드에는 httpx 가 필요합니다.") from exc
    return httpx
