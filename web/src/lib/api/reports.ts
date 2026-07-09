// 리포트 — 종합 신호 헤드라인 + 소스별 상세.

import { apiFetch } from "./core";
import type { Stock } from "./stocks";

export type SourceKey = "price" | "dart" | "hiring" | "datalab" | "patent" | "report";

export type ReportSource = {
  source: SourceKey;
  direction?: string | null;
  score?: number | null;
  data_status?: string;
  summary?: string | null;
};

export type ReportAccess = {
  unlocked: boolean;
  is_member: boolean;
};

// 긍정/주의 근거 1건 — 집계가 채운 소스별 근거(백엔드 _evidence_list 로 정규화).
// source 는 소스키(없으면 null), risk_flags 는 사람이 읽을 수 있는 한국어 설명 문자열.
export type EvidenceItem = {
  source: SourceKey | null;
  summary: string | null;
  risk_flags: string[];
};

export type Report = {
  stock: Stock;
  report_version?: {
    final_signal_id: number;
    run_key: string | null;
    signal_date: string | null;
    updated_at: string | null;
  };
  direction: string | null;
  score: number | null;
  alignment_rate: number | null;
  source_agreement?: string | null;
  // 소스 간 방향 일치도(0–100). source_agreement(HIGH/MEDIUM/LOW)와 짝으로 "소스 간 일치도" 섹션에 사용.
  consensus_score?: number | null;
  warning_level?: string | null;
  data_status?: string;
  summary: string | null;
  // 긍정/주의 한 줄 핵심 + 소스별 근거 배열(근거 중심 §5.3 3·4 섹션).
  bull_point?: string | null;
  bear_point?: string | null;
  positive_evidence?: EvidenceItem[];
  caution_evidence?: EvidenceItem[];
  sources: ReportSource[];
  access: ReportAccess;
  notice: string;
};

export type SourceDetailItem = {
  title: string | null;
  summary: string | null;
  event_date: string | null;
  direction: string | null;
  impact_level: string | null;
  evidence_url: string | null;
  source_name: string | null;
  // 소스 구분(DART/REPORT/HIRING/PATENT/DATALAB) — 타임라인 아이콘 매핑용. 소스 상세에는 없을 수 있음.
  source_type?: string | null;
  is_official?: boolean | null;
};

// 종목 근거 이벤트를 소스 교차 시간순으로 모은 타임라인(Evidence Timeline, S2).
export type ReportTimeline = {
  stock: { stock_code?: string | null; stock_name?: string | null };
  items: SourceDetailItem[];
  notice: string;
};

// 증권사 리포트 밸류에이션 fact(집계 score_breakdown.REPORT.valuation). REPORT 소스에만 존재.
export type ReportValuation = {
  target_price?: number | null;
  methodology?: string | null;
  applied_multiple?: number | null;
  implied_multiple?: number | null;
  implied_multiple_avg?: number | null;
  forward_eps_est?: number | null;
  eps_fy?: number | null;
  peer_group?: unknown[] | null;
  event_count?: number | null;
  needs_review?: boolean | null;
};

// 특허 표시 전용 구조화 데이터(score_breakdown.PATENT.patent). PATENT 소스에만 존재.
// 특허는 출원 후 ~18개월 뒤 공개되므로 두 날짜 축을 함께 보여준다.
export type PatentPublication = {
  application_no: string | null;
  title: string | null;
  application_date: string | null; // 출원일
  publication_date: string | null; // 공개일(시장 노출 시점)
  tech_category: string | null;
};

export type PatentFilingTrendPoint = {
  year: number;
  count: number;
};

export type PatentDetail = {
  recent_publications: PatentPublication[]; // 최근 *공개된* 특허(공개일 최신순)
  filing_trend: PatentFilingTrendPoint[]; // 장기 출원 추이(출원 연도별 건수)
};

// 채용 공고 1건(표시 전용, score_breakdown.HIRING.hiring.postings).
// closing_date 는 수집기가 정규화한 ISO 날짜(없으면 null). is_always_open=true 는 마감일 없는
// 상시채용 — 만료로 표시하면 안 된다. 경과일/만료 판정은 화면이 오늘 날짜로 계산한다.
export type HiringPosting = {
  job_title?: string | null;
  posting_date?: string | null;
  closing_date?: string | null;
  closing_date_display?: string | null;
  is_always_open?: boolean | null;
  source_url?: string | null;
};

// 채용 표시 전용 구조화 데이터(score_breakdown.HIRING.hiring). HIRING 소스에만 존재.
export type HiringDetail = {
  postings: HiringPosting[];
};

export type SourceDetail = {
  stock: { stock_code: string; stock_name: string | null };
  source: SourceKey;
  direction: string | null;
  score: number | null;
  data_status?: string;
  summary: string | null;
  // 분석 근거 서술 불릿(주식정보처럼 signal_events 가 없는 소스의 근거). 없으면 빈 배열.
  narrative_points?: string[] | null;
  valuation?: ReportValuation | null;
  patent?: PatentDetail | null;
  hiring?: HiringDetail | null;
  items: SourceDetailItem[];
  notice: string;
};

export async function getReport(stockCode: string): Promise<Report> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}`);
}

export async function getSourceDetail(stockCode: string, source: SourceKey): Promise<SourceDetail> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/sources/${source}`);
}

export async function getReportTimeline(stockCode: string): Promise<ReportTimeline> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/timeline`);
}

// 리포트 상단 주가 차트용 시세 시계열(현재는 데모 합성, is_demo=true).
export type PriceBar = { time: string | number; open: number; high: number; low: number; close: number };
export type PriceSeries = {
  stock_code: string;
  timeframe: string;
  currency: string;
  last_price: number;
  change: number;
  change_pct: number;
  is_demo: boolean;
  bars: PriceBar[];
};

export async function getReportPrices(stockCode: string, tf: string): Promise<PriceSeries> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/prices?tf=${encodeURIComponent(tf)}`);
}

// 과거 유사 사례(S6) — 이 종목의 과거 발화 이벤트를 (소스·방향)으로 묶어 event_study_panel 의
// 실측 20일 forward/abnormal 수익률로 사후 성과를 집계. 값은 퍼센트(%) 단위.
export type PrecedentGroup = {
  source: SourceKey | null;
  direction: string | null;
  count: number | null;
  avg_return: number | null; // 20일 실측 평균 수익률(%)
  avg_abnormal_return: number | null; // 시장(코스피20) 대비 초과수익(%)
  win_rate: number | null; // fwd_return_20d>0 비율(%)
};

export type PrecedentExample = {
  source: SourceKey | null;
  direction: string | null;
  title: string | null;
  date: string | null;
  fwd_return: number | null; // 20일 실측 수익률(%)
  abnormal_return: number | null; // 시장 대비 초과수익(%)
};

export type ReportPrecedents = {
  stock: { stock_code?: string | null; stock_name?: string | null };
  horizon_days: number;
  groups: PrecedentGroup[];
  recent: PrecedentExample[];
  notice: string;
};

export async function getReportPrecedents(stockCode: string): Promise<ReportPrecedents> {
  return apiFetch(`/api/reports/${encodeURIComponent(stockCode)}/precedents`);
}
