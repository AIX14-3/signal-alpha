"""발행물 감사 agent 데모 — 발표용(기획 §6).

감사는 "안 보이는" 기능이라, **agent trace(주장→근거조회→판정)** 를 화면에 드러내 "이건 agent다"를 보인다.
케이스 2종:
  1) 경계 조언(무게중심) — ``reject_advice`` 는 통과하지만 투자 함의가 남는 문구. LLM 이 실제 낼 법한
     회색지대라 자연스럽다. → regulation flag 기대.
  2) 조작 수치(에스컬레이션) — 서술 수치가 실데이터와 어긋남 + 초기 근거 희소. insufficient → 타깃 근거
     재조회(evidence_provider) → 재판정. → grounding 또는 unverified. **환각은 자연 발생 안 하니 주입한
     시나리오임을 정직하게 밝힌다.**

실행:
    # 실 Gemini(발표용). 무료 2.5 Flash. 키 필요.
    GEMINI_API_KEY=... uv run python services/agent-worker/script/demo_publication_audit.py
    # 오프라인 흐름 미리보기(가짜 LLM — 렌더/흐름만 확인, 발표용 아님)
    uv run python services/agent-worker/script/demo_publication_audit.py --stub
    # 판정 일관성(§8): 같은 케이스 N회 → verdict 뒤집힘 0 확인
    GEMINI_API_KEY=... uv run python services/agent-worker/script/demo_publication_audit.py --repeat 5

불변식: 감사는 점수/방향/서술을 안 바꾼다(verdict flag 만). 이 스크립트는 읽기 전용(DB 미접근).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

# 'app' 패키지 경로(services/agent-worker)를 넣어 어디서 실행하든 임포트되게 한다.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audit.agent import PublicationAuditAgent  # noqa: E402  (경로 삽입 후 임포트)
from app.audit.schema import Claim, PublicationAuditVerdict  # noqa: E402

# repo 루트 .env (services/agent-worker/script/<this> → parents[3] = repo root)
load_dotenv(Path(__file__).resolve().parents[3] / ".env")

CASE1 = {
    "name": "경계 조언(무게중심)",
    "narrative": (
        "채용을 대폭 늘리며 신사업 인력을 확충하고 있어, 향후 성장 동력이 뚜렷하고 실적 개선이 유망하다."
    ),
    "evidence": {"HIRING": {"recent_count": 42, "prev_count": 20, "top_skills": ["AI", "반도체"]}},
    "actual": None,
    "note": "reject_advice 는 통과(노골 매수/매도 없음) — LLM 이 '유망/성장 동력'의 투자 함의를 잡는다.",
}
CASE2 = {
    "name": "조작 수치(에스컬레이션 · 주입 시나리오)",
    "narrative": "채용 공고가 30건으로 급증했다.",
    "evidence": {"HIRING": {"note": "정확한 공고 건수 미첨부"}},  # 초기 근거 희소 → insufficient 유도
    "actual": {"HIRING": {"actual_posting_count": 3}},  # provider 가 재조회로 공급 → 30 vs 3 불일치
    "note": "정상 LLM 서술은 담백해 환각이 자연 발생 안 함 → '환각 방어 시연용으로 주입한' 케이스.",
}


def _make_provider(actual: dict[str, Any] | None):
    if actual is None:
        return None

    async def provider(claim: Claim, need: str | None) -> dict[str, Any]:
        return dict(actual)

    return provider


# ── 오프라인 스텁(가짜 LLM) — 렌더/흐름 확인용. adjudicate 프롬프트에만 '감사자' 포함 ──
class _StubClient:
    model = "stub"

    def __init__(self) -> None:
        self._adj_calls = 0

    async def generate_json(self, prompt: str) -> Any:
        if "감사자" not in prompt:  # 주장 추출
            # 서술 전체를 한 주장으로(스텁 단순화).
            span = prompt.split("[서술]", 1)[-1].strip() or prompt
            return [{"span": span[:60], "claim_type": "evaluative"}]
        # 판정: 키워드 라우팅
        self._adj_calls += 1
        if "유망" in prompt or "성장 동력" in prompt:
            return {"verdict": "regulation", "reason": "'유망/성장 동력'은 투자 함의 소지", "evidence": "채용 팩트만 서술 권장"}
        if "30건" in prompt:
            if self._adj_calls == 1:
                return {"verdict": "insufficient", "need": "실제 공고 건수"}
            return {"verdict": "grounding", "reason": "서술 30건 vs 실데이터 3건", "evidence": "actual_posting_count=3"}
        return {"verdict": "ok", "reason": "중립 사실"}


def _build_client(stub: bool, model: str):
    if stub:
        return _StubClient()
    from app.clients.gemini_client import GeminiError, GeminiJsonClient

    if not os.getenv("GEMINI_API_KEY"):
        raise SystemExit(
            "GEMINI_API_KEY 가 없습니다. 실 Gemini 데모는 키가 필요합니다.\n"
            "  GEMINI_API_KEY=... uv run python services/agent-worker/script/demo_publication_audit.py\n"
            "키 없이 흐름만 보려면 --stub 을 쓰세요."
        )
    try:
        return GeminiJsonClient(model=model, temperature=0.0)  # temp0 = 판정 일관성(§8)
    except GeminiError as exc:  # noqa: BLE001
        raise SystemExit(f"Gemini 클라이언트 생성 실패: {exc}")


def _print_verdict(v: PublicationAuditVerdict) -> None:
    status = "✅ 통과" if (v.compliant and v.grounding_ok and not v.flags) else "⚠️ flag"
    print(f"  판정: {status}  (compliant={v.compliant}, grounding_ok={v.grounding_ok}, hops={v.hops}, model={v.model})")
    for f in v.flags:
        ev = f" · 근거={f.evidence}" if f.evidence else ""
        print(f"    - [{f.type}] {f.reason}{ev}")
    print("  trace:")
    for step in v.trace:
        print(f"    · {step}")


# 개별 판정/재조회 실패 격리가 trace 에 남기는 마커(§8 일관성 비교에서 이 회차는 제외).
_ISOLATION_MARKERS = ("실패 격리", "재조회 실패")


def _round_excluded(v: PublicationAuditVerdict) -> bool:
    """일관성 비교에서 뺄 회차 — LLM degrade(키/구성 부재) 또는 429 등 개별 실패 격리가 있던 회차.

    (429 로 격리된 회차는 flag 셋이 달라져 안정성을 오염시킨다 → 무료티어에서 한 번의 429 로 '불안정'
    오판하는 것을 막는다. 판정 일관성은 '완전히 판정된 회차들' 사이에서만 따진다.)
    """
    return v.degraded or any(any(m in t for m in _ISOLATION_MARKERS) for t in v.trace)


async def _run_case(agent: PublicationAuditAgent, case: dict, *, repeat: int) -> None:
    print("=" * 78)
    print(f"[{case['name']}]")
    print(f"  서술: {case['narrative']}")
    print(f"  (메모: {case['note']})")
    verdicts: list[tuple] = []
    excluded = 0
    for i in range(repeat):
        v = await agent.audit(narrative=case["narrative"], evidence=dict(case["evidence"]))
        if repeat > 1:
            print(f"  -- 회차 {i + 1} --")
        _print_verdict(v)
        if _round_excluded(v):
            excluded += 1
            continue
        verdicts.append((v.compliant, v.grounding_ok, tuple(sorted(f.type for f in v.flags))))
    if repeat > 1:
        if len(verdicts) < 2:
            print(
                f"  판정 일관성(§8): ⚠️ 유효 회차 부족 — 429/degrade {excluded}회 제외 후 "
                f"{len(verdicts)}회(≥2 필요). rate 여유(또는 유료) 있을 때 재시도."
            )
        else:
            stable = len(set(verdicts)) == 1
            tag = "✅ 안정(뒤집힘 0)" if stable else "❌ 불안정 — verdict 뒤집힘"
            print(f"  판정 일관성(§8): {tag} · 유효 {len(verdicts)}/{repeat}회(429/degrade {excluded}회 제외)")


async def _main_async(args: argparse.Namespace) -> None:
    print(f"발행물 감사 agent 데모  (mode={'stub' if args.stub else 'live'}, model={args.model}, repeat={args.repeat})")
    for case in (CASE1, CASE2):
        client = _build_client(args.stub, args.model)  # 케이스마다 새 스텁(호출 카운터 리셋)
        agent = PublicationAuditAgent(client=client, evidence_provider=_make_provider(case["actual"]))
        await _run_case(agent, case, repeat=args.repeat)
    print("=" * 78)


def main() -> None:
    # Windows 콘솔(cp949)에서 한글·em대시 인코딩 크래시 방지.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass
    p = argparse.ArgumentParser(description="발행물 감사 agent 데모")
    p.add_argument("--stub", action="store_true", help="가짜 LLM 으로 오프라인 흐름 미리보기(발표용 아님)")
    p.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"), help="Gemini 모델")
    p.add_argument("--repeat", type=int, default=1, help="같은 케이스 반복 횟수(판정 일관성 §8)")
    asyncio.run(_main_async(p.parse_args()))


if __name__ == "__main__":
    main()
