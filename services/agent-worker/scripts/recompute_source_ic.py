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

정직한 유의성(엠바고): asof 격자가 촘촘해 인접 asof 의 20일 미래창이 겹치면 독립표본이 실제보다
많아 보여 순열검정이 유의성을 과대평가한다(예: 소표본 HIRING 이 겹침에선 perm-p 0.0 으로 뜬다).
``--embargo-days`` 로 종목별 asof 를 그만큼 간격으로 솎아 forward 창 겹침을 없앤다(기본 28일 ≈
20거래일). 시장중립 IC·효과크기 하한(MIN_ABS_IC)이 base-rate 함정을 잡는다면, 엠바고는 자기상관
가짜 유의를 잡는다(상호보완).

Run (services/agent-worker, LOCAL DB only):
    export DATABASE_URL="postgresql://signal_alpha:signal_alpha_password@localhost:5432/signal_alpha"
    python scripts/recompute_source_ic.py                    # 엠바고 28일(비겹침)
    python scripts/recompute_source_ic.py --embargo-days 0   # 겹침 허용(옛 동작·비교용)
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


def _embargo(labeled: list, days: int) -> list:
    """종목별로 asof 를 ``days`` 이상 간격으로 솎아 forward 창 겹침을 없앤다(정직한 독립표본).

    asof 격자가 촘촘하면 인접 asof 의 20일 미래창이 대부분 겹쳐, 실제 독립표본이 훨씬 적은데도
    순열검정이 유의성을 과대평가한다(코드 곳곳 주석의 '패널 자기상관 → p값 과대낙관'의 근원).
    종목마다 asof 오름차순으로 훑어 직전 채택 asof 로부터 days 이상 지난 것만 남긴다(그리디
    비겹침). 기본 28일 ≈ 20거래일 → 20일 forward 창이 서로 물리지 않는다. days<=0 이면 솎지
    않음(겹침 허용·옛 동작).
    """
    if days <= 0:
        return labeled
    from collections import defaultdict

    by_stock: dict = defaultdict(list)
    for row in labeled:
        by_stock[int(row["stock_id"])].append(row)
    kept: list = []
    for rows in by_stock.values():
        last: date | None = None
        for row in sorted(rows, key=lambda r: r["asof_date"]):
            a = row["asof_date"]
            if last is None or (a - last).days >= days:
                kept.append(row)
                last = a
    kept.sort(key=lambda r: (r["asof_date"], int(r["stock_id"])))
    return kept


def _demean(vals: list[float], keys: list) -> list[float]:
    """같은 key(예: asof 날짜) 그룹의 평균을 뺀 값 리스트."""
    from collections import defaultdict

    groups: dict = defaultdict(list)
    for v, k in zip(vals, keys):
        groups[k].append(v)
    means = {k: sum(v) / len(v) for k, v in groups.items()}
    return [v - means[k] for v, k in zip(vals, keys)]


def _source_metrics(triples: list[tuple[float, float, object]]) -> dict:
    """raw IC + **시장중립 IC**(같은 asof 날짜 평균을 score/return 양쪽에서 빼 공통 시장추세 제거).

    raw IC 는 상승장 base-rate(대부분 양의 수익)에 속을 수 있다 — 상승장에서 어떤 소스든 부호가
    수익과 우연히 맞으면 IC 가 부풀려진다(DART B-lite 사례: raw +0.35 → 시장중립 +0.03). 진짜
    예측력은 시장중립 IC 이며, 권장 가중은 이 값을 기준으로 한다.
    """
    pairs = [(s, r) for s, r, _ in triples]
    m = dict(_metrics(pairs))  # n, ic(raw), hit_rate, tercile_lift, perm_p
    dates = [d for _, _, d in triples]
    s_dm = _demean([s for s, _, _ in triples], dates)
    r_dm = _demean([r for _, r, _ in triples], dates)
    m["ic_neutral"] = _pearson(s_dm, r_dm)
    m["perm_p_neutral"] = _perm_pvalue(list(zip(s_dm, r_dm)), PERM_ITERS)
    return m


# 효과크기 하한: 대표본에선 IC 0.03~0.04 도 통계적 유의(p<0.05)로 뜨지만 경제적으론 노이즈다
# (설명력 ~0.1%, 패널 자기상관으로 p값 과대낙관). 트레이더블 방향신호로 인정할 최소 |IC|.
MIN_ABS_IC = 0.05


# 엠바고 스윕: 겹침(0)부터 강한 비겹침(42)까지. 진짜 방향신호라면 이 전반에서 재현돼야 한다.
# 단일 엠바고는 양끝 함정에 취약하다 — 0(겹침)=자기상관 가짜유의, 큰값=소표본 가짜유의. 서로
# 다른 소스가 각 끝에서만 떠 재현 안 되면 노이즈다(등가중 유지).
EMBARGO_SWEEP = (0, 14, 28, 42)
MIN_REPLICATION = 2  # 비겹침 엠바고(14/28/42) 중 이 횟수 이상 자격 충족해야 '재현' 인정
# 독립표본 검정력 하한: 엠바고로 겹침을 없애면 독립 N 이 급감한다(4773→수백). 이 패널은 23종목·
# 이벤트앵커라 asof 의 ~52%가 단독(1종목) 날짜 → 시장중립 IC(날짜별 demean)가 소수 다종목 날짜에
# 좌우돼 소표본서 부풀려진다(IC 가 N 줄수록 커지는 게 그 징후). 방향 IC 를 '진짜'로 인정하려면
# 자격 엠바고 실행들의 독립 N 이 최소 이만큼은 돼야 한다(그 미만은 '검증 필요 후보'로만 표기).
MIN_INDEP_N = 250


def _eligible(src_res: dict, *, include_price: bool = False) -> dict:
    """이 실행에서 자격을 만족한 소스 → 시장중립 IC. 자격: N≥MIN_N·중립IC≥MIN_ABS_IC·순열검정 통과.

    유의성만이 아니라 효과크기 하한(MIN_ABS_IC)을 함께 요구해 노이즈를 배제한다.
    """
    out = {}
    for src, m in src_res.items():
        if src == "PRICE" and not include_price:
            continue
        icn, n, p = m.get("ic_neutral"), m.get("n", 0), m.get("perm_p_neutral")
        if (icn is not None and icn >= MIN_ABS_IC and n >= MIN_N
                and p is not None and p <= PERM_P_GATE):
            out[src] = icn
    return out


def _recommend(src_res: dict, *, include_price: bool = False) -> dict:
    """단일 실행 기준 권장(참고용). 재현성 판정은 스윕(_recommend_robust)이 한다."""
    eligible = _eligible(src_res, include_price=include_price)
    if not eligible:
        return {"mode": "equal", "weights": {},
                "rationale": f"시장중립 IC≥{MIN_ABS_IC}·유의 소스 없음 → 등가중."}
    total = sum(eligible.values())
    weights = {s: 0.0 for s in SCORING_SOURCES}
    for s, icn in eligible.items():
        weights[s] = round(icn / total * len(eligible), 3)
    return {"mode": "ic", "weights": weights,
            "rationale": f"{sorted(eligible)} 가 시장중립 IC 로도 유의미 양수 → IC 비례 가중."}


def _recommend_robust(per_embargo: dict) -> dict:
    """엠바고 스윕 결과 → **재현성 + 검정력** 기반 권장. 비겹침 엠바고(>0) 중 MIN_REPLICATION
    이상에서 자격을 충족(재현)하고 **그 실행들의 독립 N 이 MIN_INDEP_N 이상**인 소스만 '진짜
    신호'로 인정한다. 재현은 되나 N 이 얇으면(소표본 아티팩트 의심) '검증 필요 후보'로만 표기.

    per_embargo: {embargo: {"labels": int, "eligible": {src: ic_neutral}, "src_res": {...}}}
    """
    nonzero = [e for e in per_embargo if e > 0]
    qualify_count: dict[str, int] = {}
    ic_by_src: dict[str, list[float]] = {}
    n_by_src: dict[str, list[int]] = {}
    for e in nonzero:
        for s, icn in per_embargo[e]["eligible"].items():
            qualify_count[s] = qualify_count.get(s, 0) + 1
            ic_by_src.setdefault(s, []).append(icn)
            n_by_src.setdefault(s, []).append(per_embargo[e]["src_res"].get(s, {}).get("n", 0))

    replicated = {s for s, c in qualify_count.items() if c >= MIN_REPLICATION}
    recommended = {s: sum(ic_by_src[s]) / len(ic_by_src[s]) for s in replicated
                   if min(n_by_src[s]) >= MIN_INDEP_N}
    candidates = sorted(replicated - set(recommended))  # 재현은 되나 검정력 부족

    if not recommended:
        cand_note = (f" 재현은 됐으나 독립 N<{MIN_INDEP_N}(소표본 아티팩트 의심): {candidates}"
                     if candidates else "")
        why = (f"{MIN_REPLICATION}회+ 재현하며 독립 N≥{MIN_INDEP_N} 인 소스 없음.{cand_note}")
        return {"mode": "equal", "weights": {}, "robust": {}, "candidates": candidates,
                "rationale": f"{why} → 등가중 유지(단일 엠바고·소표본 가짜유의 배제)."}
    total = sum(recommended.values())
    weights = {s: 0.0 for s in SCORING_SOURCES}
    for s, icn in recommended.items():
        weights[s] = round(icn / total * len(recommended), 3)
    return {"mode": "ic", "weights": weights, "robust": recommended, "candidates": candidates,
            "rationale": f"{sorted(recommended)} 가 {MIN_REPLICATION}회+ 재현 & 독립 N≥{MIN_INDEP_N} → IC 비례 가중."}


async def recompute(conn, *, asof_from, asof_to, universe, embargo_days) -> dict:
    from signal_alpha_data_access.repositories import EventStudyRepository, RawDetailRepository

    labels = await EventStudyRepository(conn).list_for_training(
        asof_from=asof_from, asof_to=asof_to, universe_snapshot=universe
    )
    labeled = [row for row in labels if row[TARGET] is not None]
    raw_n = len(labeled)
    labeled = _embargo(labeled, embargo_days)  # forward 창 비겹침 솎기(정직한 독립표본)
    stock_ids = sorted({int(row["stock_id"]) for row in labeled})
    max_asof = max(row["asof_date"] for row in labeled)
    emb_note = (
        f"엠바고 {embargo_days}일(비겹침) {raw_n}→{len(labeled)}건"
        if embargo_days > 0 else f"{len(labeled)}건(겹침 허용)"
    )
    print(f"라벨 {len(labels)}건 중 {TARGET} 유효 · {emb_note} · 종목 {len(stock_ids)}개 — 재계산 시작")

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
        print(f"  ▸ {src:8} N={m['n']:5}  raw-IC {_fmt(m['ic'])}  시장중립-IC {_fmt(m['ic_neutral'])}  "
              f"hit {_fmt(m['hit_rate'], True)}  perm-p(중립) {m['perm_p_neutral']}{note}")
    return {"labels": len(labeled), "embargo_days": embargo_days, "source_ic_20d": src_res}


def _print_replication_table(per_embargo: dict) -> None:
    """소스 × 엠바고 → 시장중립 IC(자격 충족 시 ✓). 어느 엠바고에서 뜨고 지는지 한눈에."""
    embargos = sorted(per_embargo)
    nonzero = [e for e in embargos if e > 0]
    print("\n[재현성 표 — 시장중립 IC (✓=자격: 중립IC≥%.2f·순열검정 통과·N≥%d)]" % (MIN_ABS_IC, MIN_N))
    print("  라벨수    " + "".join(f"e={e:<8}" for e in embargos))
    print("           " + "".join(f"{per_embargo[e]['labels']:<10}" for e in embargos))
    for s in [x for x in SCORING_SOURCES if x != "PRICE"]:
        if not any(per_embargo[e]["src_res"].get(s, {}).get("n") for e in embargos):
            continue  # 전 엠바고서 데이터 없음
        cells = []
        for e in embargos:
            m = per_embargo[e]["src_res"].get(s, {})
            icn = m.get("ic_neutral")
            mark = "✓" if s in per_embargo[e]["eligible"] else " "
            cells.append(f"{_fmt(icn).strip()}{mark}")
        qual = sum(1 for e in nonzero if s in per_embargo[e]["eligible"])
        print(f"  {s:8} " + "".join(f"{c:<10}" for c in cells) + f" 재현 {qual}/{len(nonzero)}")


async def main(
    json_out: str | None, asof_from: date, asof_to: date, universe: str | None, embargo_days: int | None
) -> None:
    db = os.environ.get("DATABASE_URL", "")
    _require_local(db)
    pool = await asyncpg.create_pool(dsn=db, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            if embargo_days is not None:
                # 단일 엠바고(상세 검사용). 재현성 판정은 안 함 — 참고 권장만.
                results = await recompute(
                    conn, asof_from=asof_from, asof_to=asof_to, universe=universe, embargo_days=embargo_days
                )
                out: dict = dict(results)
                out["suggested"] = _recommend(results["source_ic_20d"])
            else:
                # 기본: 엠바고 스윕 → 재현성 기반 권장(단일 엠바고 양끝 가짜유의 배제).
                per_embargo: dict = {}
                for e in EMBARGO_SWEEP:
                    res = await recompute(
                        conn, asof_from=asof_from, asof_to=asof_to, universe=universe, embargo_days=e
                    )
                    per_embargo[e] = {
                        "labels": res["labels"],
                        "eligible": _eligible(res["source_ic_20d"]),
                        "src_res": res["source_ic_20d"],
                    }
                    print()
                _print_replication_table(per_embargo)
                out = {
                    "sweep": {
                        str(e): {"labels": v["labels"],
                                 "eligible": {s: round(i, 4) for s, i in v["eligible"].items()}}
                        for e, v in per_embargo.items()
                    },
                    "suggested": _recommend_robust(per_embargo),
                }
    finally:
        await pool.close()

    suggestion = out["suggested"]
    print("\n" + "=" * 76)
    print(f"권장: weight_mode = {suggestion['mode']}  ({suggestion['rationale']})")
    if suggestion["mode"] == "ic":
        print("  → 검토 후 env 적용:")
        print("     ALT_WEIGHT_MODE=ic")
        for src, w in suggestion["weights"].items():
            if w:
                print(f"     ALT_WEIGHT_{src}={w}")
    print("해석: 단일 엠바고의 '유의'는 양끝 함정이라 신뢰 금지 — 겹침(e=0)=자기상관 가짜유의,")
    print("      큰 엠바고=소표본 가짜유의. 진짜라면 여러 엠바고서 재현+충분한 독립 N 이어야 한다.")
    print("      ⚠️ 이 패널은 23종목·이벤트앵커라 asof 의 ~52%가 단독(1종목) 날짜 → 시장중립 IC 가")
    print(f"      소수 다종목 날짜에 좌우된다. 검증 필요 후보={suggestion.get('candidates') or '없음'}.")

    if json_out:
        import json
        Path(json_out).write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
        print(f"\nJSON 요약 → {json_out}")


def _d(s: str) -> date:
    return date.fromisoformat(s)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof-from", type=_d, default=_d("2021-01-01"))
    ap.add_argument("--asof-to", type=_d, default=_d("2026-06-01"))
    ap.add_argument("--universe", default=DEFAULT_UNIVERSE, help="event_study_panel universe_snapshot (기본 kospi20_seed)")
    ap.add_argument("--embargo-days", type=int, default=None,
                    help="단일 엠바고(일)로 상세 실행. 미지정 시 스윕(0/14/28/42) + 재현성 판정(기본)")
    ap.add_argument("--json", default=None)
    args = ap.parse_args()
    asyncio.run(main(args.json, args.asof_from, args.asof_to, args.universe, args.embargo_days))
