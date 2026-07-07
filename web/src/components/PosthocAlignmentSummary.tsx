"use client";

import { useEffect, useState } from "react";
import {
  getPosthocAlignment,
  type PosthocAlignmentGroup,
  type PosthocAlignmentItem,
  type PosthocAlignmentResponse,
} from "@/lib/apiClient";

const FALLBACK_ITEMS: PosthocAlignmentItem[] = [
  {
    horizon: "7td",
    label: "7거래일",
    alignment_rate: null,
    confirmed_count: 0,
    aligned_count: 0,
    not_aligned_count: 0,
    pending_count: 0,
    sample_status: "집계 준비 중",
    direction_breakdown: [],
    first_outcome_trade_date: null,
    last_outcome_trade_date: null,
    checked_at: null,
  },
  {
    horizon: "30td",
    label: "30거래일",
    alignment_rate: null,
    confirmed_count: 0,
    aligned_count: 0,
    not_aligned_count: 0,
    pending_count: 0,
    sample_status: "확정 대기",
    direction_breakdown: [],
    first_outcome_trade_date: null,
    last_outcome_trade_date: null,
    checked_at: null,
  },
];

const FALLBACK_SIGNAL_ITEMS: PosthocAlignmentItem[] = [
  {
    horizon: "5td",
    label: "5거래일",
    alignment_rate: null,
    confirmed_count: 0,
    aligned_count: 0,
    not_aligned_count: 0,
    pending_count: 0,
    sample_status: "확정 대기",
    direction_breakdown: [],
    first_outcome_trade_date: null,
    last_outcome_trade_date: null,
    checked_at: null,
  },
];

const FALLBACK_GROUPS: PosthocAlignmentGroup[] = [
  {
    scope: "journal_based",
    metric_label: "저널 기준 사후정합성",
    items: FALLBACK_ITEMS,
  },
  {
    scope: "signal_based",
    metric_label: "전체 발행 신호 기준 사후정합성",
    items: FALLBACK_SIGNAL_ITEMS,
  },
];

const SCOPE_DESCRIPTION: Record<PosthocAlignmentGroup["scope"], string> = {
  journal_based: "사용자가 저널에 저장한 발행 당시 데이터 방향성을 기준으로 비교합니다.",
  signal_based: "전체 발행 신호 기준의 5거래일 확정 결과를 비교합니다.",
};

const DIRECTION_LABELS: Record<string, string> = {
  positive: "긍정 방향성",
  negative: "부정 방향성",
};

const FALLBACK_METHODOLOGY: PosthocAlignmentResponse["methodology"] = {
  basis: "저널에 저장된 발행 당시 데이터 방향성과 이후 확정된 관측 결과를 분리해 비교합니다.",
  included: "positive/negative 방향성이 저장되고 7거래일 또는 30거래일 outcome이 확정된 케이스",
  excluded: "neutral/mixed 방향, 확정 대기, 데이터 부족, 표본 부족 케이스",
  signal_based_basis: "전체 발행 신호 기준은 별도 검증 기록에 남은 5거래일 확정 결과만 집계합니다.",
};

const FALLBACK_NOTICE =
  "사후정합성은 과거 데이터 방향성의 검증 기록이며 미래 결과를 보장하지 않습니다. 특정 종목 행동 권유가 아니라 사용자 판단 보조를 위한 근거입니다.";

const FALLBACK_ALL_ITEMS = FALLBACK_GROUPS.flatMap((group) => group.items);
type VerificationConnectionState = "loading" | "ready" | "failed";

function displayValue(item: PosthocAlignmentItem): string {
  if (item.alignment_rate != null && item.sample_status === "집계 가능") {
    return `${item.alignment_rate.toFixed(1)}%`;
  }
  return item.sample_status;
}

function dateRange(item: PosthocAlignmentItem): string {
  if (!item.first_outcome_trade_date || !item.last_outcome_trade_date) return "확정 결과 대기";
  return `${item.first_outcome_trade_date.slice(0, 10)} ~ ${item.last_outcome_trade_date.slice(0, 10)}`;
}

function breakdownLabel(direction: string, label: string): string {
  return label || DIRECTION_LABELS[direction] || direction;
}

function latestCheckedAt(groups: PosthocAlignmentGroup[]): string | null {
  const timestamps = groups
    .flatMap((group) => group.items)
    .map((item) => item.checked_at)
    .filter((value): value is string => Boolean(value))
    .map((value) => new Date(value).getTime())
    .filter((value) => Number.isFinite(value));

  if (!timestamps.length) return null;
  return new Date(Math.max(...timestamps)).toISOString();
}

function formatVerificationTimestamp(value: string | null): string {
  if (!value) return "확정 결과 대기";
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return "확정 결과 대기";
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Seoul",
  }).format(date);
}

function verificationStatusText(state: VerificationConnectionState, checkedAt: string | null): string {
  if (state !== "ready") return "검증 데이터 연결 대기";
  return formatVerificationTimestamp(checkedAt);
}

function summaryStatus(items: PosthocAlignmentItem[]): string {
  if (items.some((item) => item.sample_status === "표본 부족")) return "표본 부족";
  if (items.some((item) => item.sample_status === "확정 대기")) return "확정 대기";
  if (items.some((item) => item.sample_status === "집계 가능")) return "집계 가능";
  return "집계 준비 중";
}

export function PosthocAlignmentSummary() {
  const [groups, setGroups] = useState<PosthocAlignmentGroup[]>(FALLBACK_GROUPS);
  const [status, setStatus] = useState(summaryStatus(FALLBACK_ALL_ITEMS));
  const [methodology, setMethodology] =
    useState<PosthocAlignmentResponse["methodology"]>(FALLBACK_METHODOLOGY);
  const [notice, setNotice] = useState(FALLBACK_NOTICE);
  const [verificationState, setVerificationState] = useState<VerificationConnectionState>("loading");
  const [latestVerificationAt, setLatestVerificationAt] = useState<string | null>(latestCheckedAt(FALLBACK_GROUPS));

  useEffect(() => {
    getPosthocAlignment()
      .then((data) => {
        const nextGroups = data.groups?.length ? data.groups : FALLBACK_GROUPS;
        const nextItems = nextGroups.flatMap((group) => group.items);
        setGroups(nextGroups);
        setStatus(summaryStatus(nextItems));
        setMethodology(data.methodology);
        setNotice(data.notice);
        setLatestVerificationAt(latestCheckedAt(nextGroups));
        setVerificationState("ready");
      })
      .catch(() => {
        setGroups(FALLBACK_GROUPS);
        setStatus(summaryStatus(FALLBACK_ALL_ITEMS));
        setMethodology(FALLBACK_METHODOLOGY);
        setNotice(FALLBACK_NOTICE);
        setLatestVerificationAt(null);
        setVerificationState("failed");
      });
  }, []);

  return (
    <section className="mt-8 space-y-4" aria-label="저널 기준 사후정합성 요약" data-flow="posthoc-alignment-summary">
      {groups.map((group) => (
        <div key={group.scope}>
          <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <div>
              <h2 className="text-[17px] font-bold">{group.metric_label}</h2>
              <p className="mt-1 text-[12.5px] text-muted">{SCOPE_DESCRIPTION[group.scope]}</p>
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {group.items.map((item) => (
              <div key={`${group.scope}-${item.horizon}`} className="card p-5">
                <div className="text-[13px] font-semibold text-muted">{item.label} 사후정합성</div>
                <div className="mt-2 text-[24px] font-extrabold text-navy">{displayValue(item)}</div>
                <p className="mt-2 text-[13px] leading-6 text-navy-soft">
                  확정 {item.confirmed_count.toLocaleString("ko-KR")}건 · 대기 {item.pending_count.toLocaleString("ko-KR")}건
                </p>
                <p className="mt-1 text-[12.5px] leading-5 text-navy-soft">
                  정합 {item.aligned_count.toLocaleString("ko-KR")}건 · 비정합 {item.not_aligned_count.toLocaleString("ko-KR")}건
                </p>
                <p className="mt-1 text-[12.5px] leading-5 text-muted">{dateRange(item)}</p>
                {item.direction_breakdown.length ? (
                  <div className="mt-3 space-y-2 border-t border-line pt-3">
                    {item.direction_breakdown.map((breakdown) => (
                      <div key={breakdown.direction} className="text-[12.5px] leading-5 text-navy-soft">
                        <div className="font-semibold text-navy">
                          {breakdownLabel(breakdown.direction, breakdown.label)}
                        </div>
                        <div>
                          확정 {breakdown.confirmed_count.toLocaleString("ko-KR")}건 · 정합{" "}
                          {breakdown.aligned_count.toLocaleString("ko-KR")}건 · 비정합{" "}
                          {breakdown.not_aligned_count.toLocaleString("ko-KR")}건 · 대기{" "}
                          {breakdown.pending_count.toLocaleString("ko-KR")}건
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ))}
      <div className="card p-5">
        <div className="text-[13px] font-semibold text-muted">표본 상태</div>
        <div className="mt-2 text-[24px] font-extrabold text-navy">{status}</div>
        <p className="mt-2 text-[13px] leading-6 text-navy-soft">
          비율보다 확정 건수와 표본 한계를 먼저 확인합니다.
        </p>
      </div>
      <div className="card p-5" data-flow="posthoc-verification-status">
        <div className="text-[13px] font-semibold text-muted">마지막 검증 반영</div>
        <div className="mt-2 text-[20px] font-extrabold text-navy">
          {verificationStatusText(verificationState, latestVerificationAt)}
        </div>
        <p className="mt-2 text-[13px] leading-6 text-navy-soft">
          확정 결과가 반영된 가장 최근 시점을 기준으로 표시합니다.
        </p>
      </div>
      <div className="card p-5" data-flow="posthoc-methodology-detail">
        <div className="text-[13px] font-semibold text-muted">검증 기준</div>
        <div className="mt-3 grid grid-cols-1 gap-3 md:grid-cols-2">
          <div>
            <div className="text-[12.5px] font-bold text-navy">기준</div>
            <p className="mt-1 text-[13px] leading-6 text-navy-soft">{methodology.basis}</p>
          </div>
          <div>
            <div className="text-[12.5px] font-bold text-navy">포함 기준</div>
            <p className="mt-1 text-[13px] leading-6 text-navy-soft">{methodology.included}</p>
          </div>
          <div>
            <div className="text-[12.5px] font-bold text-navy">제외 기준</div>
            <p className="mt-1 text-[13px] leading-6 text-navy-soft">{methodology.excluded}</p>
          </div>
          {methodology.signal_based_basis ? (
            <div>
              <div className="text-[12.5px] font-bold text-navy">전체 발행 신호 기준</div>
              <p className="mt-1 text-[13px] leading-6 text-navy-soft">{methodology.signal_based_basis}</p>
            </div>
          ) : null}
        </div>
        <p className="mt-4 border-t border-line pt-3 text-[12.5px] leading-6 text-muted">{notice}</p>
      </div>
    </section>
  );
}
