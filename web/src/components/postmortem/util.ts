// 매매 부검 표기 헬퍼 — 3분류/신호 라벨, 손익 포맷(한국 관례: 상승=빨강, 하락=파랑).

import type { Classification, ObservedSignal } from "@/lib/apiClient";

export function verdictLabel(c: Classification): { title: string; tone: "neutral" | "warn" | "ok" } {
  switch (c.verdict) {
    case "observable_signal":
      return { title: "그때 볼 수 있었던 신호가 있었습니다", tone: "warn" };
    case "hindsight_only":
      // 사후 고점만 아는 것 = 실수 아님(차별화 포인트).
      return { title: "그 시점엔 관측 가능한 신호가 없었습니다 — 실수가 아닙니다", tone: "ok" };
    case "open":
      return { title: "미청산 포지션", tone: "neutral" };
    default:
      return { title: "계획대로였거나 부진하지 않았습니다", tone: "ok" };
  }
}

export function signalKindLabel(kind: ObservedSignal["kind"]): string {
  return kind === "insider_sell" ? "내부자 매도 공시" : "내부자 매수 공시";
}

// 등락률 색: 한국 관례로 상승=빨강(text-red), 하락=파랑(text-sky-deep).
export function pnlClass(pct: number | null): string {
  if (pct === null) return "text-muted";
  if (pct > 0) return "text-red";
  if (pct < 0) return "text-sky-deep";
  return "text-navy-soft";
}

export function formatPct(pct: number | null | undefined): string {
  if (pct === null || pct === undefined) return "—";
  const sign = pct > 0 ? "+" : "";
  return `${sign}${pct.toFixed(2)}%`;
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  return iso.slice(0, 10).replace(/-/g, ".");
}

export function formatWon(value: string | null): string {
  if (!value) return "—";
  const n = Number(value);
  if (Number.isNaN(n)) return value;
  return `${n.toLocaleString("ko-KR")}원`;
}
