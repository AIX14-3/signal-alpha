"""Render REPORT.md from backtest results."""

from __future__ import annotations

from datetime import datetime

from config import HORIZONS, REPORT_PATH
from universe import EXCLUDED, UNIVERSE


def _pct(x) -> str:
    return f"{x*100:.1f}%" if isinstance(x, (int, float)) else "-"


def _verdict(per_horizon: dict) -> str:
    lifts = [per_horizon[h]["rule"]["all"].get("lift_vs_majority", 0) for h in HORIZONS]
    best = max(lifts)
    if best <= 0.005:
        return ("**결론: NO-GO 신호.** 어떤 기간에서도 규칙 모델이 단순 다수클래스 베이스라인을 "
                "유의하게(>0.5%p) 넘지 못함 — 기술적 지표만으로는 예측 우위가 확인되지 않음.")
    if best < 0.02:
        return ("**결론: 미미한 우위.** 베이스라인을 약간 넘지만(0.5~2%p) 거래비용·과적합을 고려하면 "
                "실거래 우위로 보긴 어려움. 추가 검증 필요.")
    return ("**결론: 검토 가치 있는 우위.** 일부 기간에서 베이스라인을 2%p+ 상회 — 단, 누수 placebo와 "
            "거래비용 확인 후에만 신뢰.")


def build_report(results: dict, chart_files: list[str], llm: dict | None) -> None:
    ph = results["per_horizon"]
    L: list[str] = []
    L.append("# AI 선도주 기술적분석 예측 적중률 — 백테스트 리포트")
    L.append("")
    L.append(f"- 생성: {datetime.now().isoformat(timespec='seconds')}")
    L.append(f"- 유니버스: {', '.join(i.symbol for i in UNIVERSE)} ({len(UNIVERSE)}종목)")
    L.append(f"- 제외: " + "; ".join(f"{k}({v})" for k, v in EXCLUDED.items()))
    L.append("- 방법: 워크포워드 OOS, 익일/1주/1개월 방향, 규칙 vs ML, 국면(전체/AI기) 비교")
    L.append("")

    L.append("## 1) 기간별 OOS 적중률 (전 종목 풀링)")
    L.append("")
    L.append("| 기간 | 모델 | 표본 | 적중률 | 다수클래스 | Buy&Hold | 리프트 | F1(up) |")
    L.append("|------|------|------|--------|-----------|----------|--------|--------|")
    for h in HORIZONS:
        for m in ("rule", "ml"):
            d = ph[h][m]["all"]
            L.append(f"| {h} | {m} | {d.get('n','-')} | {_pct(d.get('accuracy'))} | "
                     f"{_pct(d.get('majority_acc'))} | {_pct(d.get('buy_hold_acc'))} | "
                     f"{d.get('lift_vs_majority',0)*100:+.1f}%p | {_pct(d.get('f1_up'))} |")
    L.append("")
    L.append("> 리프트 = 적중률 − 다수클래스 베이스라인. **양수라야 의미**가 있음.")
    L.append("")

    L.append("## 2) 국면 비교 (규칙 모델)")
    L.append("")
    L.append("| 기간 | 전체 10년 | Pre-AI | AI기(2022-11+) |")
    L.append("|------|-----------|--------|----------------|")
    for h in HORIZONS:
        r = ph[h]["rule"]
        L.append(f"| {h} | {_pct(r['all'].get('accuracy'))} | "
                 f"{_pct(r['pre_ai'].get('accuracy'))} | {_pct(r['ai_era'].get('accuracy'))} |")
    L.append("")

    L.append("## 3) 누수 자가검증 (placebo)")
    L.append("")
    L.append("라벨을 무작위로 섞어 예측과 맞춰본 적중률. **~50%면 누수 없음**(정상).")
    L.append("")
    for h in HORIZONS:
        L.append(f"- {h}: {_pct(results['placebo'].get(h))}")
    L.append("")

    L.append("## 4) 종목별 적중률 (1주, 규칙, OOS)")
    L.append("")
    L.append("| 종목 | 표본 | 적중률 | 리프트 |")
    L.append("|------|------|--------|--------|")
    for sym, hd in results["per_symbol"].items():
        d = hd["1w"]["rule"]
        L.append(f"| {sym} | {d.get('n','-')} | {_pct(d.get('accuracy'))} | "
                 f"{d.get('lift_vs_majority',0)*100:+.1f}%p |")
    L.append("")

    L.append("## 5) LLM 판단 vs 규칙 (동일 표본)")
    L.append("")
    if llm is None:
        L.append("LLM API 키 미설정 → 스킵. (.env에 GEMINI_API_KEY 또는 OPENAI_API_KEY 설정 후 재실행)")
    else:
        L.append("| 기간 | LLM 적중률 | 규칙 적중률(동일표본) | 표본 |")
        L.append("|------|-----------|----------------------|------|")
        for h, d in llm.items():
            L.append(f"| {h} | {_pct(d['llm'].get('accuracy'))} | "
                     f"{_pct(d['rule_same'].get('accuracy'))} | {d['llm'].get('n','-')} |")
        L.append("")
        L.append("> LLM이 규칙보다 높지 않으면, 숫자 판단에 LLM을 끼울 근거가 약함.")
    L.append("")

    L.append("## 6) 차트")
    L.append("")
    for f in chart_files:
        L.append(f"![{f}](charts/{f})")
    L.append("")

    L.append("## 7) 결론 및 한계")
    L.append("")
    L.append(_verdict(ph))
    L.append("")
    L.append("**한계(반드시 감안):**")
    L.append("- 유니버스가 AI/반도체로 상관↑ → 독립 표본 적음. '승자만 선택'한 선택편향 존재.")
    L.append("- 익일은 본질적으로 잡음 지배 → 50% 근처가 정상. 높으면 누수 의심(placebo 확인).")
    L.append("- 누적수익 차트의 거래비용은 단순 가정치이며 실거래 슬리피지·체결 미반영.")
    L.append("- 본 결과는 분석 방법론 검증용이며 투자 자문이 아님.")

    REPORT_PATH.write_text("\n".join(L), encoding="utf-8")
