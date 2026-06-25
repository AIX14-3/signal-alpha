"""메타러너 시드 스윕 드라이버 — train_meta.py 를 시드별로 돌려 MVP 베이스라인 분포를 만든다.

설계 이유:
  ``train_meta.py --result-json`` 은 시드 1개 결과만 남긴다. 스윕을 구동하는 재현·재개 가능한
  드라이버가 없으면 (PowerShell 종료 같은) 중단 시 진행 상태가 통째로 날아간다.
  이 드라이버는 ``out/seed<S>.json`` 존재 시 **스킵**(재개)하므로 몇 번을 끊겨도 안전하게 완주한다.

흐름:
  ① 시드 범위(예: 1-30)를 순회. 각 시드의 ``out/seed<S>.json`` 이 유효하고 **현재 스윕 파라미터
     (synthetic·sessions)로 생성됐으면** 스킵. 구버전/다른 설정 파일은 재계산(집계 오염 방지).
  ② 없거나 파라미터 불일치면 ``train_meta.py --synthetic N --seed S --result-json out/seed<S>.json`` 자식 실행.
  ③ 전 시드 결과를 읽어 집계 → ``out/meta_sweep_summary.csv`` + ``out/meta_sweep_summary.json``.
  ④ adopted/equal 비율로 **MVP 메타러너 베이스라인 결정**을 출력(합성 데이터는 equal 유지가 정상).

  uv run python tools/harness/sweep_seeds.py --seeds 1-30
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import json
import statistics
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve()
HARNESS_DIR = HERE.parent
TRAIN_META = HARNESS_DIR / "train_meta.py"
DEFAULT_OUT = HARNESS_DIR / "out"

# 채택 가능 후보로 볼 최소 adopted 비율(합성 데이터에선 보통 미달 → equal 유지가 정상).
ADOPT_RATIO_THRESHOLD = 0.80
# 합성인데 이 정도가 가드를 "통과"하면 가드 임계값이 느슨하다는 신호(튜닝 권고만, 자동 변경 X).
GUARD_LEAK_WARN_RATIO = 0.30


def parse_seeds(spec: str) -> list[int]:
    """"1-30" / "1,2,5" / "1-3,7,10-12" 혼합 파싱."""
    seeds: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            seeds.extend(range(int(lo), int(hi) + 1))
        else:
            seeds.append(int(part))
    # 중복 제거 + 정렬(재현성).
    return sorted(dict.fromkeys(seeds))


def _valid_result(path: Path) -> dict[str, Any] | None:
    """결과 JSON 이 파싱 가능하고 핵심 키를 가지면 dict, 아니면 None(재계산 필요)."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "winner" not in data or "board" not in data:
        return None
    return data


def _params_match(data: dict[str, Any], *, synthetic: int, sessions: int) -> bool:
    """캐시된 결과가 현재 스윕 파라미터로 생성됐는지(누락 키 = 구버전 → 불일치 처리)."""
    return data.get("synthetic_n") == synthetic and data.get("sessions") == sessions


def run_seed(seed: int, *, synthetic: int, sessions: int, out_path: Path) -> tuple[dict[str, Any] | None, str]:
    """train_meta.py 자식 프로세스 실행. (파싱된 결과 dict | None, 메시지)."""
    cmd = [
        sys.executable,
        str(TRAIN_META),
        "--synthetic", str(synthetic),
        "--sessions", str(sessions),
        "--seed", str(seed),
        "--result-json", str(out_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-3:]
        return None, "  ".join(tail) or f"exit {proc.returncode}"
    data = _valid_result(out_path)
    if data is None:
        return None, "ran but produced no valid result json"
    return data, "ok"


def _row_by_method(board: list[dict[str, Any]], method: str) -> dict[str, Any] | None:
    return next((r for r in board if r.get("method") == method), None)


def aggregate(results: dict[int, dict[str, Any]]) -> dict[str, Any]:
    """시드별 결과 → 집계 통계 + 베이스라인 결정."""
    per_seed: list[dict[str, Any]] = []
    nnls_max_weights: list[float] = []
    improve_pcts: list[float] = []
    model_weight_sums: dict[str, float] = {}
    model_weight_n = 0
    adopted = 0

    for seed in sorted(results):
        data = results[seed]
        board = data["board"]
        winner = data["winner"]
        reason = data.get("reason", "")
        nnls = _row_by_method(board, "nnls")
        equal = _row_by_method(board, "equal")
        is_adopted = winner != "equal"
        if is_adopted:
            adopted += 1

        nnls_max = float(nnls["max_weight"]) if nnls else None
        nnls_q = float(nnls["holdout_qlike"]) if nnls else None
        equal_q = float(equal["holdout_qlike"]) if equal else None
        # equal_q==0(완벽 적합, 사실상 불가)면 None — 0 나눗셈 방지.
        improve = ((equal_q - nnls_q) / equal_q * 100.0) if (nnls and equal_q) else None

        if nnls:
            nnls_max_weights.append(nnls_max)
            if improve is not None:
                improve_pcts.append(improve)
            for m, w in nnls.get("weights", {}).items():
                model_weight_sums[m] = model_weight_sums.get(m, 0.0) + float(w)
            model_weight_n += 1

        per_seed.append({
            "seed": seed,
            "winner": winner,
            "reason": reason,
            "nnls_max_weight": round(nnls_max, 4) if nnls_max is not None else None,
            "nnls_holdout_qlike": round(nnls_q, 6) if nnls_q is not None else None,
            "equal_holdout_qlike": round(equal_q, 6) if equal_q is not None else None,
            "improve_pct": round(improve, 3) if improve is not None else None,
            "oof_rows": int(data.get("oof_rows", 0)),
        })

    total = len(per_seed)
    adopt_ratio = adopted / total if total else 0.0
    avg_model_weights = (
        {m: round(s / model_weight_n, 4) for m, s in sorted(model_weight_sums.items())}
        if model_weight_n else {}
    )

    def _dist(xs: list[float]) -> dict[str, float] | None:
        if not xs:
            return None
        return {
            "min": round(min(xs), 4),
            "median": round(statistics.median(xs), 4),
            "max": round(max(xs), 4),
        }

    # ---- 베이스라인 결정 ----
    if adopt_ratio >= ADOPT_RATIO_THRESHOLD:
        decision = "candidate_adopt"
        rationale = (
            f"nnls 가 시드 {adopt_ratio:.0%} 에서 가드를 통과 — 실가중 채택 후보. "
            "단, 합성 데이터이므로 실 OHLCV 백필 후 재검증 권장."
        )
    else:
        decision = "keep_equal_fallback"
        rationale = (
            f"nnls 채택 {adopt_ratio:.0%} (<{ADOPT_RATIO_THRESHOLD:.0%}) — 합성 데이터 과적합 차단 위해 "
            "equal_fallback 유지(설계 의도 일치)."
        )

    guard_note = None
    if GUARD_LEAK_WARN_RATIO <= adopt_ratio < ADOPT_RATIO_THRESHOLD:
        guard_note = (
            f"합성 데이터인데 {adopt_ratio:.0%} 가 가드를 통과 — conc_cap/min_improve 가 느슨할 수 있음. "
            "후속 작업으로 임계값 재조정 검토(이번엔 기록만, 변경 안 함)."
        )

    return {
        "total_seeds": total,
        "adopted": adopted,
        "equal_fallback": total - adopted,
        "adopt_ratio": round(adopt_ratio, 4),
        "nnls_max_weight_dist": _dist(nnls_max_weights),
        "nnls_improve_pct_dist": _dist(improve_pcts),
        "avg_nnls_model_weights": avg_model_weights,
        "baseline_decision": decision,
        "decision_rationale": rationale,
        "guard_note": guard_note,
        "per_seed": per_seed,
    }


def write_outputs(summary: dict[str, Any], out_dir: Path) -> tuple[Path, Path]:
    csv_path = out_dir / "meta_sweep_summary.csv"
    json_path = out_dir / "meta_sweep_summary.json"
    fields = [
        "seed", "winner", "reason", "nnls_max_weight",
        "nnls_holdout_qlike", "equal_holdout_qlike", "improve_pct", "oof_rows",
    ]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(summary["per_seed"])
    json_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return csv_path, json_path


def _print_summary(summary: dict[str, Any], csv_path: Path, json_path: Path) -> None:
    print("\n" + "=" * 72)
    print("META-LEARNER SEED SWEEP — SUMMARY")
    print("=" * 72)
    print(f"seeds={summary['total_seeds']}  adopted={summary['adopted']}  "
          f"equal_fallback={summary['equal_fallback']}  adopt_ratio={summary['adopt_ratio']:.0%}")
    if summary["nnls_max_weight_dist"]:
        d = summary["nnls_max_weight_dist"]
        print(f"nnls max_weight   min/median/max = {d['min']}/{d['median']}/{d['max']}  (conc_cap=0.85)")
    if summary["nnls_improve_pct_dist"]:
        d = summary["nnls_improve_pct_dist"]
        print(f"nnls vs equal QLIKE improve%  min/median/max = {d['min']}/{d['median']}/{d['max']}")
    if summary["avg_nnls_model_weights"]:
        ws = "  ".join(f"{m}={w}" for m, w in summary["avg_nnls_model_weights"].items())
        print(f"avg nnls weights  {ws}")
    print("-" * 72)
    print(f"BASELINE DECISION: {summary['baseline_decision']}")
    print(f"  {summary['decision_rationale']}")
    if summary["guard_note"]:
        print(f"⚠  {summary['guard_note']}")
    print("-" * 72)
    print(f"📊 per-seed → {csv_path}")
    print(f"📦 summary  → {json_path}")


def main() -> None:
    # Windows cp949 콘솔에서도 em-dash/이모지 출력 가능하도록(자식은 mvp_runtime 가 처리).
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.reconfigure(encoding="utf-8", errors="replace")

    p = argparse.ArgumentParser(description="Sweep meta-learner over synthetic seeds (resumable).")
    p.add_argument("--seeds", default="1-30", help='Seed spec: "1-30", "1,2,5", "1-3,7" (default 1-30).')
    p.add_argument("--synthetic", type=int, default=12, help="Synthetic tickers per run (default 12).")
    p.add_argument("--sessions", type=int, default=500, help="Sessions per synthetic ticker (default 500).")
    p.add_argument("--out-dir", default=str(DEFAULT_OUT), dest="out_dir")
    p.add_argument("--force", action="store_true", help="Recompute even if out/seed<S>.json exists.")
    args = p.parse_args()

    seeds = parse_seeds(args.seeds)
    if not seeds:
        raise SystemExit(f"No seeds parsed from --seeds={args.seeds!r} (e.g. '1-30', '1,2,5').")
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    if not TRAIN_META.exists():
        raise SystemExit(f"train_meta.py not found at {TRAIN_META}")

    print(f"Sweep {len(seeds)} seeds {seeds[0]}..{seeds[-1]}  (synthetic={args.synthetic} sessions={args.sessions})")
    results: dict[int, dict[str, Any]] = {}
    failures: list[int] = []

    for i, seed in enumerate(seeds, 1):
        out_path = out_dir / f"seed{seed}.json"
        cached = None if args.force else _valid_result(out_path)
        # 파라미터가 현재 스윕과 일치할 때만 재사용 — 구버전/다른 설정 파일은 재계산(집계 오염 방지).
        if cached is not None and _params_match(cached, synthetic=args.synthetic, sessions=args.sessions):
            results[seed] = cached
            print(f"[{i:>3}/{len(seeds)}] seed {seed:<4} skip(exists)  winner={cached['winner']}")
            continue
        if cached is not None:
            print(f"[{i:>3}/{len(seeds)}] seed {seed:<4} recompute(param mismatch)")
        data, msg = run_seed(seed, synthetic=args.synthetic, sessions=args.sessions, out_path=out_path)
        if data is None:
            failures.append(seed)
            print(f"[{i:>3}/{len(seeds)}] seed {seed:<4} FAILED  {msg}")
            continue
        results[seed] = data
        print(f"[{i:>3}/{len(seeds)}] seed {seed:<4} winner={data['winner']:<6} ({data.get('reason','')})")

    if not results:
        raise SystemExit("No seed results to aggregate.")

    summary = aggregate(results)
    summary["failed_seeds"] = failures
    csv_path, json_path = write_outputs(summary, out_dir)
    _print_summary(summary, csv_path, json_path)
    if failures:
        print(f"⚠  {len(failures)} seed(s) failed: {failures} — rerun to retry (resumable).")


if __name__ == "__main__":
    main()
