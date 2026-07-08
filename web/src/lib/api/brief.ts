// 데일리 브리핑(S3) — 오늘 발행된 신호를 종목별 카드로 모은 전체 공개 대시보드.

import { apiFetch } from "./core";

export type BriefItem = {
  stock_id: number;
  stock: {
    id?: number;
    stock_code?: string | null;
    stock_name?: string | null;
    market?: string | null;
  };
  direction: string | null; // POSITIVE | NEGATIVE | NEUTRAL | MIXED (대문자)
  score: number | null; // 0–100
  alignment_rate?: number | null;
  source_agreement?: string | null;
  warning_level?: string | null;
  data_status?: string;
  summary: string | null;
};

export type Brief = { items: BriefItem[]; count: number; notice: string };

export async function getBrief(limit = 30): Promise<Brief> {
  return apiFetch(`/api/brief?limit=${limit}`, { auth: "none" });
}
