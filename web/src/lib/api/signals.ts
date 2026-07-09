// 종목별 현재 신호 배치 조회 — 관심종목 "추적" 화면의 재료.
//
// 브리핑(/api/brief)이 "오늘 뭘 볼까"(발견)라면, 이 엔드포인트는 "내가 담은 종목에
// 무슨 일이 있었나"(추적)에 답한다. 그래서 소스별 분해와 직전 발행본 대비 변화량을 함께 준다.

import { apiFetch } from "./core";

export type SourceScore = {
  direction: string; // POSITIVE | NEGATIVE | NEUTRAL | MIXED | UNKNOWN (대문자)
  score: number | null; // 0–100
  data_status: string; // ok | partial | no_signal | missing | failed
};

// 직전 발행본 대비 변화. score_delta 가 null 이면 "변화 없음"이 아니라 **비교 불가**다
// (직전 발행본이 없거나, 너무 오래됐거나). previous_signal_date 로 어느 쪽인지 구분한다.
export type SignalChange = {
  signal_date: string | null;
  previous_signal_date: string | null;
  previous_score: number | null;
  previous_direction: string | null;
  score_delta: number | null;
};

export type SignalListItem = {
  stock_id: number;
  stock: {
    id?: number;
    stock_code?: string | null;
    stock_name?: string | null;
    market?: string | null;
  };
  direction: string | null;
  score: number | null; // 6소스 통합 점수
  alignment_rate?: number | null;
  source_agreement?: string | null;
  warning_level?: string | null;
  data_status?: string;
  summary: string | null;
  score_breakdown: { sources: Record<string, SourceScore> };
  change: SignalChange;
};

/** 지정 종목들의 현재 신호(+변화량). 로그인 필요. 빈 배열이면 요청하지 않는다. */
export async function getSignalsByStockIds(stockIds: number[]): Promise<SignalListItem[]> {
  if (stockIds.length === 0) return [];
  return apiFetch(`/api/signals?stock_ids=${stockIds.join(",")}`);
}
