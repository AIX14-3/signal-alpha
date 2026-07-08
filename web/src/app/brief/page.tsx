"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getBrief, type BriefItem } from "@/lib/apiClient";
import { directionLabel, warningLevelLabel } from "@/lib/format";

// S3 데일리 브리핑 — 오늘 발행된 신호를 종목별 카드로 모은 전체 공개 대시보드.
// 정렬은 백엔드가 "먼저 봐야 할 것"(주의 레벨 → 방향 세기) 순으로 준다.

function tone(t: "up" | "down" | "flat"): string {
  return t === "up" ? "up" : t === "down" ? "down" : "flat";
}

export default function BriefPage() {
  const [items, setItems] = useState<BriefItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getBrief(30)
      .then((d) => setItems(d.items))
      .catch((e) => setError((e as Error).message));
  }, []);

  return (
    <div className="relative py-8" data-page="brief">
      <div className="report-aura pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />

      <header className="mb-6">
        <h1 className="text-[30px] font-extrabold">데일리 브리핑</h1>
        <p className="mt-1 text-[13.5px] text-muted">
          오늘 발행된 신호를 한눈에 — 주의가 필요하거나 방향이 뚜렷한 종목부터 보여드려요.
          {items && <span className="ml-1 font-semibold text-navy-soft">총 {items.length}건</span>}
        </p>
      </header>

      {error ? (
        <div className="glass p-6 text-center">
          <p className="text-[14px] text-muted">브리핑을 불러오지 못했습니다.</p>
        </div>
      ) : items === null ? (
        <p className="py-16 text-center text-muted">브리핑을 불러오는 중…</p>
      ) : items.length === 0 ? (
        <div className="glass p-8 text-center">
          <p className="text-[14px] text-navy-soft">아직 발행된 신호가 없습니다.</p>
          <Link href="/" className="mt-3 inline-block text-[13.5px] font-semibold text-sky-deep">
            ← 종목 검색하러 가기
          </Link>
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((it) => (
            <BriefCard key={it.stock_id} item={it} />
          ))}
        </div>
      )}
    </div>
  );
}

function BriefCard({ item }: { item: BriefItem }) {
  const dir = directionLabel(item.direction);
  const warn = warningLevelLabel(item.warning_level);
  const code = item.stock.stock_code ?? "";
  const hasScore = item.score != null;

  return (
    <Link
      href={`/report/${encodeURIComponent(code)}`}
      className="glass hover-lift block p-5"
      data-flow="brief-card"
      data-stock={code}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="truncate text-[15px] font-bold">{item.stock.stock_name ?? code}</div>
          <div className="text-[12px] text-muted">
            {code} · {item.stock.market ?? "—"}
          </div>
        </div>
        <span className={`pill ${tone(dir.tone)} shrink-0`} style={{ padding: "3px 9px", fontSize: 12 }}>
          {dir.label}
        </span>
      </div>

      <div className="mt-3 flex items-baseline gap-2">
        {hasScore ? (
          <>
            <span className="text-[32px] font-extrabold leading-none">{Math.round(item.score as number)}</span>
            <span className="text-[13px] font-semibold text-muted">/100</span>
          </>
        ) : (
          <span className="text-[15px] font-bold text-muted">점수 없음</span>
        )}
        {warn.severity !== "normal" && (
          <span className="pill down ml-auto" style={{ padding: "3px 9px", fontSize: 11.5 }}>
            {warn.label}
          </span>
        )}
      </div>

      <p className="mt-2 min-h-[38px] line-clamp-2 text-[13px] text-navy-soft">
        {item.summary ?? "요약이 없습니다."}
      </p>
      <div className="mt-2 text-[13px] font-semibold text-sky-deep">리포트 보기 →</div>
    </Link>
  );
}
