// 백엔드 응답 → 시안 표기 변환 규칙 (frontend-architecture.md §5-1, §5-2).

export const NOTICE_FALLBACK =
  "Signal Alpha는 매수·매도 추천이 아니라 데이터 방향성과 근거를 제공하는 서비스입니다.";

/** final_score(0–100) → 시안 /10 표기 (예: 75 → 7.5). */
export function scoreOutOf10(finalScore: number | null | undefined): string {
  if (finalScore === null || finalScore === undefined) return "–";
  return (Math.round((finalScore / 10) * 10) / 10).toFixed(1);
}

/** alignment_rate(0–1) → 백분율. */
export function alignmentPercent(rate: number | null | undefined): string {
  if (rate === null || rate === undefined) return "–";
  return `${Math.round(rate * 100)}%`;
}

/** source_agreement → 신뢰도 라벨. */
export function agreementLabel(agreement: string | null | undefined): string {
  switch ((agreement ?? "").toUpperCase()) {
    case "HIGH":
      return "높음";
    case "MEDIUM":
      return "보통";
    case "LOW":
      return "낮음";
    default:
      return "–";
  }
}

/** direction → 데이터 방향성 라벨 (투자 권유 표현 금지). */
export function directionLabel(direction: string | null | undefined): {
  label: string;
  tone: "up" | "down" | "flat";
} {
  switch ((direction ?? "").toUpperCase()) {
    case "POSITIVE":
      return { label: "매수 우위", tone: "up" };
    case "NEGATIVE":
      return { label: "매도 우위", tone: "down" };
    case "MIXED":
      return { label: "혼조", tone: "flat" };
    default:
      return { label: "중립", tone: "flat" };
  }
}

/**
 * 시안 6타일 팩터 ↔ 백엔드 4소스 매핑 (잠정, 팀 확정 필요).
 * DART→재무+공시, PRICE→수급+시계열, REPORT+ALTERNATIVE→뉴스.
 */
export const FACTOR_MAP: { label: string; source: string; hint: string }[] = [
  { label: "재무 건전성", source: "DART", hint: "수익성·성장성" },
  { label: "뉴스 감성", source: "REPORT", hint: "리포트·대체데이터" },
  { label: "수급 모멘텀", source: "PRICE", hint: "투자자별 매매" },
  { label: "시계열 추세", source: "PRICE", hint: "일봉 추세 모델" },
  { label: "공시 이벤트", source: "DART", hint: "실적·지분·리스크" },
];

export function won(amount: number): string {
  return `₩${amount.toLocaleString("ko-KR")}`;
}
