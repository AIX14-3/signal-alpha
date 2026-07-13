"""Vertex 에서 Claude 가 실제로 호출되는지 검증한다 (할당량 증액 후 확인용).

429 "Quota exceeded" → 할당량이 아직 0 이거나 반영 전.
404 "not found"      → 모델 ID/리전이 틀렸거나 접근 불가.
200                  → 사용 가능. A/B 로 넘어가면 된다.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.error
import urllib.request

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

PROJECT = "signal-alpha-demo"
# Claude 는 이 프로젝트에서 global 리전 전용으로 서빙된다(us-east5/europe-west1 은 404).
CANDIDATES = [
    ("global", "claude-haiku-4-5"),
    ("global", "claude-sonnet-4-5"),
]


def _token() -> str:
    # Windows 의 gcloud 는 .cmd 라 shell=True 가 필요하다.
    out = subprocess.run(
        "gcloud auth application-default print-access-token",
        shell=True, capture_output=True, text=True,
    )
    token = out.stdout.strip()
    if not token:
        raise SystemExit(f"ADC 토큰 없음. `gcloud auth application-default login` 필요.\n{out.stderr[:200]}")
    return token


def main() -> None:
    token = _token()
    for location, model in CANDIDATES:
        host = "aiplatform.googleapis.com" if location == "global" else f"{location}-aiplatform.googleapis.com"
        url = (
            f"https://{host}/v1/projects/{PROJECT}/locations/{location}"
            f"/publishers/anthropic/models/{model}:rawPredict"
        )
        body = {
            "anthropic_version": "vertex-2023-10-16",
            "max_tokens": 64,
            "messages": [{"role": "user", "content": '반드시 JSON만: {"ok": true}'}],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            text = "".join(b.get("text", "") for b in payload.get("content", []))
            usage = payload.get("usage", {})
            print(f"✅ 사용 가능  {location}/{model}")
            print(f"   응답: {text.strip()[:60]}")
            print(f"   토큰: {usage}")
        except urllib.error.HTTPError as exc:
            detail = json.loads(exc.read().decode("utf-8")).get("error", {}).get("message", "")
            if exc.code == 429:
                print(f"⏳ 할당량 0/미반영  {location}/{model}")
                print("   → enable_claude_quota.sh 실행 또는 반영 대기(수 분 소요 가능)")
            elif exc.code == 404:
                print(f"❌ 모델 접근 불가  {location}/{model}")
            else:
                print(f"❌ HTTP {exc.code}  {location}/{model}")
            print(f"   {detail[:150]}")


if __name__ == "__main__":
    main()
