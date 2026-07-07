"use client";

import { useEffect, useState } from "react";
import {
  getPosthocAlignment,
  type PosthocAlignmentGroup,
  type PosthocAlignmentItem,
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

function summaryStatus(items: PosthocAlignmentItem[]): string {
  if (items.some((item) => item.sample_status === "표본 부족")) return "표본 부족";
  if (items.some((item) => item.sample_status === "확정 대기")) return "확정 대기";
  if (items.some((item) => item.sample_status === "집계 가능")) return "집계 가능";
  return "집계 준비 중";
}

export function PosthocAlignmentSummary() {
  const [groups, setGroups] = useState<PosthocAlignmentGroup[]>(FALLBACK_GROUPS);
  const [status, setStatus] = useState(summaryStatus(FALLBACK_ITEMS));

  useEffect(() => {
    getPosthocAlignment()
      .then((data) => {
        const nextGroups = data.groups?.length ? data.groups : FALLBACK_GROUPS;
        const nextItems = nextGroups.flatMap((group) => group.items);
        setGroups(nextGroups);
        setStatus(summaryStatus(nextItems));
      })
      .catch(() => {
        setGroups(FALLBACK_GROUPS);
        setStatus(summaryStatus(FALLBACK_ITEMS));
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
    </section>
  );
}
