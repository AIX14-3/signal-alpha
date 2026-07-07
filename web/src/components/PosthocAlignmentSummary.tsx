"use client";

import { useEffect, useState } from "react";
import { getPosthocAlignment, type PosthocAlignmentItem } from "@/lib/apiClient";

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
  const [items, setItems] = useState<PosthocAlignmentItem[]>(FALLBACK_ITEMS);
  const [status, setStatus] = useState(summaryStatus(FALLBACK_ITEMS));

  useEffect(() => {
    getPosthocAlignment()
      .then((data) => {
        const nextItems = data.items.length ? data.items : FALLBACK_ITEMS;
        setItems(nextItems);
        setStatus(summaryStatus(nextItems));
      })
      .catch(() => {
        setItems(FALLBACK_ITEMS);
        setStatus(summaryStatus(FALLBACK_ITEMS));
      });
  }, []);

  return (
    <section className="mt-8 grid grid-cols-1 gap-4 md:grid-cols-3" aria-label="저널 기준 사후정합성 요약" data-flow="posthoc-alignment-summary">
      {items.map((item) => (
        <div key={item.horizon} className="card p-5">
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
