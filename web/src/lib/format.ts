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
 * 소스 카드 data_status → 사용자 표기.
 * - ok/partial: 점수를 대신 노출하므로 빈 문자열
 * - no_signal: 수집은 됐으나 유의 시그널 없음
 * - missing: 아직 수집/분석 전 (집계 시점에 해당 소스 결과 행이 없음)
 * - failed: 수집·분석 오류
 */
export function dataStatusLabel(status: string | null | undefined): string {
  switch ((status ?? "").toLowerCase()) {
    case "ok":
    case "partial":
      return "";
    case "no_signal":
      return "시그널이 없습니다";
    case "missing":
      return "데이터 수집 전";
    case "failed":
      return "분석 실패";
    default:
      return "—";
  }
}

/** ok/partial 이면 점수, 그 외에는 상태 라벨(시그널 없음/수집 전/실패)을 노출. */
export function sourceStatusLine(
  status: string | null | undefined,
  score: number | null | undefined,
): string {
  const s = (status ?? "").toLowerCase();
  if (s === "ok" || s === "partial") {
    return `점수 ${score ?? "–"}`;
  }
  return dataStatusLabel(status);
}

/** 리포트 5개 연결점 소스 키 → 한글 라벨/아이콘/힌트. */
export const SOURCE_META: Record<
  string,
  { label: string; icon: string; hint: string }
> = {
  price: { label: "주식정보", icon: "📈", hint: "시세·재무 지표" },
  dart: { label: "DART", icon: "📑", hint: "전자공시" },
  hiring: { label: "채용공고", icon: "🧑‍💼", hint: "채용 동향" },
  datalab: { label: "네이버 키워드", icon: "🔎", hint: "검색 관심도" },
  report: { label: "증권사 리포트", icon: "🏦", hint: "애널리스트 의견" },
};

export const SOURCE_ORDER: ("price" | "dart" | "hiring" | "datalab" | "report")[] = [
  "price",
  "dart",
  "hiring",
  "datalab",
  "report",
];

export function sourceLabel(source: string): string {
  return SOURCE_META[source]?.label ?? source;
}

export function won(amount: number): string {
  return `₩${amount.toLocaleString("ko-KR")}`;
}
