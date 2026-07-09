"""recompute-IC: 과거 시점(event_study_panel asof)에 소스별 점수를 **재계산**해 이미 있는
선행수익률 라벨과 잇고, 소스별 IC 를 실측한다.

왜 재계산인가: 저장된 final_signals 는 전부 최근 신호라 20일 선행수익률 구간이 아직 안 지나
(미래 가격 없음) IC 측정 불가. 반면 event_study_panel 은 과거 asof + forward return 라벨을
이미 갖고 있으므로, 그 과거 시점에 원본에서 소스 점수를 재계산하면 실제 IC 를 잴 수 있다.

경로(프로덕션과 동일 + 누수차단):
  라벨(EventStudyRepository) → 각 (stock, asof) 에서 소스 evidence 로더로 원본 로드 →
  pit_rows(known_at ≤ asof, 미래행 차단) → compute_indicators → evaluate_indicators → score →
  (score, fwd_return_20d) 쌍 → 소스별 IC/hit/순열검정 → 권장 ALT_WEIGHT (ic_diagnostic 재사용).

스코프: 대체데이터 3소스(PATENT·DATALAB·HIRING). PRICE/DART/REPORT 제외.

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    python scripts/recompute_source_ic.py
    python scripts/recompute_source_ic.py --asof-from 2021-01-01 --asof-to 2026-06-01 --json out.json
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

_HERE = Path(__file__).resolve()
sys.path.insert(0, str(_HERE.parents[1]))  # agent-worker 루트 (app.* 임포트)
sys.path.insert(0, str(_HERE.parents[3] / "packages" / "data-access"))  # signal_alpha_data_access
sys.path.insert(0, str(_HERE.parent))  # scripts (ic_diagnostic 재사용)

import asyncpg  # noqa: E402

from app.analyzers.config import (  # noqa: E402
    DartRuleConfig,
    DataLabRuleConfig,
    HiringRuleConfig,
    PatentRuleConfig,
)
from app.analyzers.dart.source_result import build_dart_analysis_result  # noqa: E402
from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators  # noqa: E402
from app.analyzers.datalab.rules import evaluate_indicators as datalab_eval  # noqa: E402
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators  # noqa: E402
from app.analyzers.hiring.rules import evaluate_indicators as hiring_eval  # noqa: E402
from app.analyzers.patent.indicators import compute_indicators as patent_indicators  # noqa: E402
from app.analyzers.patent.rules import evaluate_indicators as patent_eval  # noqa: E402
from app.analyzers.price.analyzer import PriceAnalyzer  # noqa: E402
from app.ml.source_features import KNOWN_AT, pit_rows  # noqa: E402
from app.ml.train_source_models import _PriceTrainingLoader, _build_loader  # noqa: E402
from ic_diagnostic import (  # noqa: E402
    MIN_N,
    PERM_ITERS,
    PERM_P_GATE,
    SCORING_SOURCES,
    _fmt,
    _metrics,
    _pearson,
    _perm_pvalue,
)

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

TARGET = "fwd_return_20d"
DEFAULT_UNIVERSE = "kospi20_seed"

# (SRC, kind, loader_key, date_key, indicators_fn, eval_fn, config_cls). kind:
#   rules = compute_indicators + evaluate_indicators (대체데이터 3소스)
#   dart  = build_dart_analysis_result(events).score (결정론 DART 점수)
#   price = PriceAnalyzer().analyze (⚠️ 자기참조: 과거가격→미래가격, 대체데이터 알파 아님)
SOURCES = [
    ("PATENT", "rules", "patent", KNOWN_AT["patent"], patent_indicators, patent_eval, PatentRuleConfig),
    ("DATALAB", "rules", "datalab", KNOWN_AT["datalab"], datalab_indicators, datalab_eval, DataLabRuleConfig),
    ("HIRING", "rules", "hiring", KNOWN_AT["hiring"], hiring_indicators, hiring_eval, HiringRuleConfig),
    ("DART", "dart", "dart", KNOWN_AT["dart"], None, None, DartRuleConfig),
    ("PRICE", "price", "price", KNOWN_AT["price"], None, None, None),
]
# REPORT 제외: 결정론 분석기/로더 경로가 없고(밸류에이션 별도 경로) 로컬 데이터 27건·최근이라
# 20일 선행수익률 미경과 → 측정 불가. 데이터·경로 확보 시 별도.


def _dart_blite_events(pit: list[dict]) -> list[dict]:
    """DART ownership 이벤트에 B-lite 방향/임팩트를 부여한다(프로덕션 코드 불변, 하니스 전용).

    build_dart_analysis_result 의 B-lite 는 event['signal_direction']·['impact_level'] 로 임팩트
    가중 순극성을 낸다. 수집된 ownership 이벤트엔 이 파생 필드가 없어 상수 0(no_signal)이 된다.
    소스 주석(source_result.py:161)이 명시한 대로 **내부자 shares_delta 부호**를 방향으로,
    ratio_delta 크기를 임팩트로 매핑해 B-lite 경로를 활성화한다(#805 의도의 근사).
    """
    out = []
    for e in pit:
        d = dict(e)
        sd = e.get("shares_delta")
        d["signal_direction"] = (
            "positive" if (sd is not None and float(sd) > 0)
            else "negative" if (sd is not None and float(sd) < 0)
            else "unknown"
        )
        rd = e.get("ratio_delta")
        mag = abs(float(rd)) if rd is not None else 0.0
        d["impact_level"] = "high" if mag >= 1.0 else "medium" if mag >= 0.2 else "low"
        out.append(d)
    return out


async def _score(kind, pit, asof, cfg, sector, ind_fn, eval_fn) -> float:
    """소스 kind 별 점수 재계산(누수차단된 pit 행 입력). signed [-1, +1] 반환."""
    if kind == "dart":
        return build_dart_analysis_result(_dart_blite_events(pit)).score
    if kind == "price":
        res = await PriceAnalyzer().analyze("", [SimpleNamespace(metadata={"rows": pit})])
        return res.score
    # rules (patent/datalab/hiring)
    lookback = getattr(cfg, "lookback_days", 30)
    if ind_fn is hiring_indicators:
        ind = ind_fn(pit, as_of=asof, lookback_days=lookback, sector_demand=sector)
    else:
        ind = ind_fn(pit, as_of=asof, lookback_days=lookback)
    return eval_fn(ind, cfg).score


def _require_local(db_url: str) -> None:
    if not any(h in db_url for h in ("localhost", "127.0.0.1")):
        raise SystemExit(f"refusing: DATABASE_URL host not local ({db_url.split('@')[-1]}).")


def _demean(vals: list[float], keys: list) -> list[float]:
    """같은 key(예: asof 날짜) 그룹의 평균을 뺀 값 리스트."""
    from collections import defaultdict

    groups: dict = defaultdict(list)
    for v, k in zip(vals, keys):
        groups[k].append(v)
    means = {k: sum(v) / len(v) for k, v in groups.items()}
    return [v - means[k] for v, k in zip(vals, keys)]


WF_BLOCKS = 3  # 워크포워드 시간분할 블록 수(연속 날짜 구간)
STRONG_Q = 2.0 / 3.0  # 조건부 IC: |score-50| 상위 1/3(신호가 강한 표본)만


def _neutral_ic(triples: list[tuple[float, float, object]]) -> float | None:
    """시장중립 IC = 같은 asof 날짜 평균을 score/return 양쪽에서 뺀 뒤 피어슨."""
    if len(triples) < 3:
        return None
    dates = [t[2] for t in triples]
    s = _demean([t[0] for t in triples], dates)
    r = _demean([t[1] for t in triples], dates)
    return _pearson(s, r)


def _strong_subset(triples: list[tuple[float, float, object]]) -> list:
    """조건부 IC용 — |score-50|(신호 강도) 상위 1/3 표본. 결과가 아니라 '결정 시점에 아는'
    신호 강도로만 조건 → 선택편향 없음. '점수가 강할 때 더 예측적인가?'를 본다."""
    if not triples:
        return []
    mags = sorted(abs(t[0] - 50.0) for t in triples)
    thr = mags[min(int(len(mags) * STRONG_Q), len(mags) - 1)]
    return [t for t in triples if abs(t[0] - 50.0) >= thr]


def _walkforward_ics(triples: list[tuple[float, float, object]], k: int = WF_BLOCKS) -> list:
    """asof 날짜를 연속 k블록으로 나눠 블록별 시장중립 IC. 한 구간(레짐)만 좋고 다른 구간에서
    무너지는 아티팩트(DART 상승장 등)를 드러낸다 = 과거 데이터로 흉내낸 OOS."""
    uniq = sorted({t[2] for t in triples})
    if len(uniq) < k:
        return []
    size = len(uniq) / k
    ics = []
    for i in range(k):
        block = set(uniq[int(i * size):int((i + 1) * size)])
        ics.append(_neutral_ic([t for t in triples if t[2] in block]))
    return ics


def _source_metrics(triples: list[tuple[float, float, object]]) -> dict:
    """raw IC + **시장중립 IC** + 조건부(강신호) IC + 워크포워드(블록별) IC.

    raw IC 는 상승장 base-rate 에 속고(DART raw +0.35 → 중립 +0.03), 대표본에선 미미한 IC 도
    유의로 뜬다. 그래서 (1) 시장중립(공통추세 제거), (2) 조건부(강신호일 때 예측적인가), (3)
    워크포워드(여러 시간구간서 일관되게 살아남는가)를 함께 재 진짜 신호만 남긴다.
    """
    pairs = [(s, r) for s, r, _ in triples]
    m = dict(_metrics(pairs))  # n, ic(raw), hit_rate, tercile_lift, perm_p
    dates = [d for _, _, d in triples]
    s_dm = _demean([s for s, _, _ in triples], dates)
    r_dm = _demean([r for _, r, _ in triples], dates)
    m["ic_neutral"] = _pearson(s_dm, r_dm)
    m["perm_p_neutral"] = _perm_pvalue(list(zip(s_dm, r_dm)), PERM_ITERS)
    m["ic_neutral_strong"] = _neutral_ic(_strong_subset(triples))  # 조건부 IC
    m["wf_ics"] = _walkforward_ics(triples)  # 블록별 시장중립 IC(시간 일관성)
    return m


# 효과크기 하한: 대표본에선 IC 0.03~0.04 도 통계적 유의(p<0.05)로 뜨지만 경제적으론 노이즈다
# (설명력 ~0.1%, 패널 자기상관으로 p값 과대낙관). 트레이더블 방향신호로 인정할 최소 |IC|.
MIN_ABS_IC = 0.05


def _recommend(src_res: dict, *, include_price: bool = False) -> dict:
    """권장 가중 자격(전부 충족): N≥MIN_N · 시장중립 IC≥MIN_ABS_IC · 순열검정 통과 ·
    **모든 워크포워드 블록에서 시장중립 IC>0**(시간 일관성=흉내낸 OOS).

    시간 일관성 요건이 단일 레짐 아티팩트(DART 상승장 raw IC)를 배제한다 — 한 구간만 좋고
    다른 구간에서 뒤집히면 탈락.
    """
    eligible = {}
    for src, m in src_res.items():
        if src == "PRICE" and not include_price:
            continue
        icn, n, p = m.get("ic_neutral"), m.get("n", 0), m.get("perm_p_neutral")
        wf = m.get("wf_ics") or []
        wf_ok = len(wf) >= 2 and all(x is not None and x > 0 for x in wf)
        if (icn is not None and icn >= MIN_ABS_IC and n >= MIN_N
                and p is not None and p <= PERM_P_GATE and wf_ok):
            eligible[src] = icn
    if not eligible:
        return {"mode": "equal", "weights": {},
                "rationale": (f"시장중립 IC≥{MIN_ABS_IC}·유의·전구간 일관 양수를 모두 만족하는 "
                              "소스 없음 → 등가중 유지(단일레짐 아티팩트·대표본 노이즈 배제).")}
    total = sum(eligible.values())
    weights = {s: 0.0 for s in SCORING_SOURCES}
    for s, icn in eligible.items():
        weights[s] = round(icn / total * len(eligible), 3)
    return {"mode": "ic", "weights": weights,
            "rationale": f"{sorted(eligible)} 가 시장중립 IC 로도 유의미 양수 → IC 비례 가중."}


async def recompute(conn, *, asof_from, asof_to, universe) -> dict:
    from signal_alpha_data_access.repositories import EventStudyRepository, RawDetailRepository

    labels = await EventStudyRepository(conn).list_for_training(
        asof_from=asof_from, asof_to=asof_to, universe_snapshot=universe
    )
    labeled = [row for row in labels if row[TARGET] is not None]
    stock_ids = sorted({int(row["stock_id"]) for row in labeled})
    max_asof = max(row["asof_date"] for row in labeled)
    print(f"라벨 {len(labels)}건 중 {TARGET} 유효 {len(labeled)}건 · 종목 {len(stock_ids)}개 — 재계산 시작")

    repo = RawDetailRepository(conn)
    src_res: dict[str, dict] = {}
    for src, kind, loader_key, date_key, ind_fn, eval_fn, cfg_cls in SOURCES:
        cfg = cfg_cls.from_env() if cfg_cls else None
        # 종목당 전 이력을 1회만 로드(as_of=max_asof, 큰 창) → 메모리에서 asof 별 pit 필터.
        # (stock,asof) 마다 DB 재조회하던 O(라벨수) 쿼리를 O(종목수)로 줄인다. 누수는 pit_rows 차단.
        if kind == "price":
            loader = _PriceTrainingLoader(conn, window_days=3000)
        else:
            loader = _build_loader(loader_key, repo, max(getattr(cfg, "lookback_days", 0), 3000), connection=conn)
        rows_by_stock: dict[int, list] = {}
        sector_by_stock: dict[int, object] = {}
        for sid in stock_ids:
            ev = await loader.load(stock_id=sid, stock_code="", as_of=max_asof)
            rows_by_stock[sid] = list(ev[0].metadata.get("rows") or []) if ev else []
            if loader_key == "hiring" and ev:
                sector_by_stock[sid] = ev[0].metadata.get("sector_demand")

        triples: list[tuple[float, float, object]] = []
        for row in labeled:
            sid = int(row["stock_id"])
            asof = row["asof_date"]
            pit = pit_rows(rows_by_stock.get(sid) or [], asof, date_key=date_key)
            if not pit:
                continue  # 소스가 그 시점에 침묵(행 0)
            score = await _score(kind, pit, asof, cfg, sector_by_stock.get(sid), ind_fn, eval_fn)
            # 0-100 스케일 변환(ic_diagnostic._metrics/_hit_rate 는 중심 50 가정). IC 는 스케일 불변.
            triples.append((score * 50.0 + 50.0, float(row[TARGET]), asof))

        src_res[src] = _source_metrics(triples)
        m = src_res[src]
        note = " (⚠️자기참조)" if src == "PRICE" else ""
        wf = "[" + ",".join(_fmt(x).strip() for x in (m.get("wf_ics") or [])) + "]"
        print(f"  ▸ {src:8} N={m['n']:5}  중립-IC {_fmt(m['ic_neutral'])}  "
              f"강신호-IC {_fmt(m['ic_neutral_strong'])}  워크포워드 {wf}  perm-p {m['perm_p_neutral']}{note}")
    return {"labels": len(labeled), "source_ic_20d": src_res}


async def main(json_out: str | None, asof_from: date, asof_to: date, universe: str | None) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            results = await recompute(conn, asof_from=asof_from, asof_to=asof_to, universe=universe)
    finally:
        await pool.close()

    print("\n" + "=" * 76)
    print(f"recompute-IC — 재계산 표본 라벨 {results['labels']}건 · 20일 지평선 · 5소스(대체3+DART+주가, REPORT 제외)")
    print("=" * 76)
    suggestion = _recommend(results["source_ic_20d"], include_price=False)
    results["suggested"] = suggestion
    print(f"권장: weight_mode = {suggestion['mode']}  ({suggestion['rationale']})")
    if suggestion["mode"] == "ic":
        print("  → 검토 후 env 적용:")
        print("     ALT_WEIGHT_MODE=ic")
        for src, w in suggestion["weights"].items():
            print(f"     ALT_WEIGHT_{src}={w}")
    print("해석: IC 부호=재계산 점수와 선행수익률 상관(양수=예측적). 대체데이터 방향알파는")
    print("      선례상 대체로 null — null 이면 등가중 유지가 정답(가중=노이즈 과적합 방지).")

    if json_out:
        import json
        Path(json_out).write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON 요약 → {json_out}")


def _d(s: str) -> date:
    return date.fromisoformat(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof-from", type=_d, default=_d("2021-01-01"))
    ap.add_argument("--asof-to", type=_d, default=_d("2026-06-01"))
    ap.add_argument("--universe", default=DEFAULT_UNIVERSE, help="event_study_panel universe_snapshot (기본 kospi20_seed)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    asyncio.run(main(args.json, args.asof_from, args.asof_to, args.universe))
