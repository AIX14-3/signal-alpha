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

/** warning_level → 사용자 표기 + 심각도(색 구분용). 투자 권유 아님, 데이터 확인 안내. */
export function warningLevelLabel(level: string | null | undefined): {
  label: string;
  severity: "normal" | "caution" | "warning";
} {
  switch ((level ?? "").toUpperCase()) {
    case "WARNING":
      return { label: "경고", severity: "warning" };
    case "CAUTION":
      return { label: "주의", severity: "caution" };
    case "NORMAL":
      return { label: "양호", severity: "normal" };
    default:
      return { label: "—", severity: "normal" };
  }
}

/** consensus_score(0–100, 소스 간 방향 일치도) → 백분율 표기. */
export function consensusPercent(score: number | null | undefined): string {
  if (score === null || score === undefined) return "–";
  return `${Math.round(score)}%`;
}

/** direction → 데이터 방향성 라벨 (투자 권유 표현 금지). */
export function directionLabel(direction: string | null | undefined): {
  label: string;
  tone: "up" | "down" | "flat";
} {
  switch ((direction ?? "").toUpperCase()) {
    case "POSITIVE":
      return { label: "긍정 방향", tone: "up" };
    case "NEGATIVE":
      return { label: "부정 방향", tone: "down" };
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
  datalab: { label: "검색량", icon: "🔎", hint: "검색 관심도" },
  patent: { label: "특허", icon: "💡", hint: "특허 출원 동향" },
  report: { label: "증권사 리포트", icon: "🏦", hint: "애널리스트 의견" },
};

// 표시 순서 — 시장이 이미 반영한 것(주가·리포트·공시)을 앞에, 아직 가격에 덜 반영된
// 대체데이터 흔적(검색량·특허·채용)을 뒤에 둔다.
export const SOURCE_ORDER: (
  | "price"
  | "dart"
  | "hiring"
  | "datalab"
  | "patent"
  | "report"
)[] = ["price", "report", "dart", "datalab", "patent", "hiring"];

export function sourceLabel(source: string): string {
  return SOURCE_META[source]?.label ?? source;
}

export function won(amount: number): string {
  return `₩${amount.toLocaleString("ko-KR")}`;
}

// 요약 화면(리포트 헤더·서류철 카드)의 점수 표기 — 소수 첫째 자리에서 **버림**한다.
// 반올림하지 않는 이유: 68.05 를 68.1 로 올리면 없는 정밀도를 만들어 낸다.
// 상세 보고서는 원본 점수를 그대로 노출하므로 이 함수를 쓰지 않는다.
//
// toFixed 로 한 번 걸러 낸 뒤 버림하는 건 방어다. `score * 10` 이 정수 바로 아래로 떨어지면
// (이진 부동소수점 표현 오차) 버림이 한 칸 내려간다. 0~100 을 0.001 간격으로 전수 확인한
// 범위에서는 실제 반례가 없었지만, 점수 소수 자릿수는 집계기 구현에 달려 있어 고정하지 않는다.
export function scoreText(score: number | null | undefined): string {
  if (score == null || !Number.isFinite(score)) return "–";
  const truncated = Math.floor(Number((score * 10).toFixed(6))) / 10;
  // 정수는 소수점을 붙이지 않는다(70.0 → 70).
  return Number.isInteger(truncated) ? String(truncated) : truncated.toFixed(1);
}

/** 오늘(KST) 자정 기준 Date. 경과일은 시각이 아니라 '날짜' 단위로 세야 한다. */
function todayKST(): Date {
  const now = new Date();
  const kst = new Date(now.toLocaleString("en-US", { timeZone: "Asia/Seoul" }));
  return new Date(kst.getFullYear(), kst.getMonth(), kst.getDate());
}

/** "YYYY-MM-DD" → 로컬 자정 Date. 파싱 실패는 null. `new Date("2026-07-09")`는 UTC 자정이라 쓰지 않는다. */
function parseISODate(value: string | null | undefined): Date | null {
  if (!value) return null;
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!m) return null;
  const d = new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
  return Number.isNaN(d.getTime()) ? null : d;
}

const DAY_MS = 86_400_000;

/** 기준일로부터 오늘까지 경과한 일수(음수 = 미래). 파싱 실패는 null. */
export function daysSince(date: string | null | undefined): number | null {
  const d = parseISODate(date);
  if (!d) return null;
  return Math.round((todayKST().getTime() - d.getTime()) / DAY_MS);
}

/** 경과일을 사람이 읽는 표기로. "오늘" / "어제" / "N일 전". 미래면 "D-N". */
export function relativeDayLabel(date: string | null | undefined): string | null {
  const n = daysSince(date);
  if (n === null) return null;
  if (n < 0) return `D${n}`; // 미래(예: 마감일) — D-3 형태
  if (n === 0) return "오늘";
  if (n === 1) return "어제";
  return `${n}일 전`;
}

/** 마감일이 지났는지. 마감일 당일은 아직 유효(만료 아님). 날짜 없음/파싱실패는 false. */
export function isExpired(closingDate: string | null | undefined): boolean {
  const n = daysSince(closingDate);
  return n !== null && n > 0;
}

/** 출원연도가 "아직 다 안 들어온" 것으로 보는 기간. 공개 시차 중앙값이 ~18개월이라
 * 출원연도 Y의 공개분은 대체로 Y+2 중에 마무리된다. */
export const FILING_TREND_INCOMPLETE_YEARS = 2;

/** 추이의 각 연도에 "아직 집계 중" 여부를 붙인다(행은 버리지 않는다).
 *
 * 수집된 특허는 **이미 공개된 것**뿐인데 공개는 출원보다 ~18개월 늦다. 그래서 최근
 * 출원연도는 실제 출원 건수보다 적게 잡히고, 아무 표시 없이 그리면 R&D가 급감한 것처럼
 * 읽힌다(예: 2023년 9,204건 → 2025년 279건). 값은 그대로 노출하되 미완성임을 밝힌다.
 */
export function markIncompleteFilingYears<T extends { year: number }>(
  trend: T[],
): (T & { incomplete: boolean })[] {
  const cutoff = todayKST().getFullYear() - FILING_TREND_INCOMPLETE_YEARS;
  return trend.map((d) => ({ ...d, incomplete: d.year > cutoff }));
}

// DB 타임스탬프(UTC ISO)를 KST 로 표기. 원문을 그대로 slice 하면 UTC 시각이 로컬처럼
// 보여 9시간 어긋난다(예: 차단 "재개 예정" 이 실제보다 이르게 표시). null/파싱실패는 "-".
export function dateTimeKST(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? value
    : d.toLocaleString("ko-KR", { timeZone: "Asia/Seoul" });
}

// 뉴스 목록용 짧은 KST 표기 "MM.DD HH:mm". 원본 ISO(UTC)를 직접 slice 하면 9시간
// 어긋나고, dateTimeKST 의 로케일 문자열을 slice 하면 로케일 의존으로 깨진다 —
// Date 파싱 후 KST 파트로 조립한다. null/파싱실패는 "-".
export function dateTimeShortKST(value: string | null | undefined): string {
  if (!value) return "-";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return value;
  const parts = new Intl.DateTimeFormat("ko-KR", {
    timeZone: "Asia/Seoul",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(d);
  const get = (type: string) => parts.find((p) => p.type === type)?.value ?? "";
  return `${get("month")}.${get("day")} ${get("hour")}:${get("minute")}`;
}

// 수집/LLM 유래 URL은 신뢰할 수 없다 — href 로 렌더하기 전 http(s) 스킴만 허용한다.
// javascript:/data:/vbscript: 등은 클릭 시 앱 오리진에서 스크립트가 실행돼 토큰 탈취로 이어질 수 있다.
export function safeHttpUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const u = new URL(value, "https://invalid.local");
    return u.protocol === "http:" || u.protocol === "https:" ? value : null;
  } catch {
    return null;
  }
}
