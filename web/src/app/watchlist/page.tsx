"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { getSignalsByStockIds, type SignalListItem } from "@/lib/apiClient";
import { directionLabel, scoreText, sourceLabel, warningLevelLabel } from "@/lib/format";
import { useAuthStore } from "@/stores/authStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 관심종목 = **추적** 화면. 브리핑(/brief)이 모르는 종목을 훑는 "발견"이라면, 여기는 이미 아는
// 종목에 무슨 일이 있었는지 본다. 그래서 카드 그리드가 아니라 밀집 행이고, 시선의 주인공은
// 종목명이 아니라 **변화량**이며, 정렬축도 점수 세기가 아니라 변화량이다.

const SOURCE_KEYS = ["dart", "price", "report", "datalab", "patent", "hiring"] as const;

/** 변화량 표기. null 은 "변화 없음"이 아니라 비교 불가다 — 다르게 보여 준다. */
function DeltaBadge({ item }: { item: SignalListItem }) {
  const delta = item.change.score_delta;
  if (delta === null) {
    const reason = item.change.previous_signal_date ? "직전 발행이 오래됨" : "비교할 이전 신호 없음";
    return (
      <span className="text-[12px] text-muted" title={reason}>
        —
      </span>
    );
  }
  if (delta === 0) return <span className="text-[12px] text-muted">변화 없음</span>;
  // 등락 색은 한국 관례(상승 빨강 / 하락 파랑).
  const up = delta > 0;
  return (
    <span className={`text-[14px] font-bold ${up ? "text-rose-600" : "text-blue-600"}`}>
      {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
    </span>
  );
}

/** 소스별 점수를 작은 게이지로. 브리핑 카드에 없는 정보 — 두 화면을 가르는 축. */
function SourceGauges({ item }: { item: SignalListItem }) {
  const sources = item.score_breakdown?.sources ?? {};
  return (
    <div className="flex flex-wrap gap-x-3 gap-y-1">
      {SOURCE_KEYS.map((key) => {
        const src = sources[key];
        const score = src?.score ?? null;
        const missing = !src || score === null || src.data_status === "missing";
        return (
          <span key={key} className="flex items-center gap-1 text-[11px] text-muted">
            <span>{sourceLabel(key)}</span>
            {missing ? (
              <span aria-label="데이터 없음">—</span>
            ) : (
              <span
                className="inline-block h-1.5 w-8 rounded-full bg-line"
                title={`${sourceLabel(key)} ${scoreText(score)}점`}
              >
                <span
                  className="block h-full rounded-full bg-sky-deep"
                  style={{ width: `${Math.max(4, Math.min(100, score))}%` }}
                />
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}

function WatchRow({ item, code }: { item: SignalListItem; code: string }) {
  const dir = directionLabel(item.direction);
  const warn = warningLevelLabel(item.warning_level);
  const changed = item.change.score_delta !== null && item.change.score_delta !== 0;

  return (
    <Link
      href={`/report/${encodeURIComponent(code)}`}
      className={`block border-t border-line px-3 py-3.5 transition first:border-0 hover:bg-black/[0.02] ${
        changed ? "" : "opacity-70"
      }`}
      data-flow="watchlist-row"
      data-stock={code}
    >
      <div className="flex items-center gap-3">
        {warn.severity !== "normal" && (
          <span className="shrink-0 text-[13px] text-amber-600" title={warn.label}>
            ⚠
          </span>
        )}
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-[14.5px] font-bold">{item.stock.stock_name ?? code}</span>
            <span className="text-[11.5px] text-muted">{code}</span>
          </div>
          <div className="mt-1.5">
            <SourceGauges item={item} />
          </div>
        </div>

        <div className="shrink-0 text-right">
          <DeltaBadge item={item} />
          <div className="mt-0.5 text-[12px] text-muted">
            {scoreText(item.score)}점 · {dir.label}
          </div>
        </div>
      </div>
    </Link>
  );
}

export default function WatchlistPage() {
  const status = useAuthStore((s) => s.status);
  const items = useWatchlistStore((s) => s.items);
  const loaded = useWatchlistStore((s) => s.loaded);
  const ensureLoaded = useWatchlistStore((s) => s.ensureLoaded);
  const [signals, setSignals] = useState<Record<number, SignalListItem> | null>(null);

  useEffect(() => {
    if (status === "authenticated") void ensureLoaded();
  }, [status, ensureLoaded]);

  const stockIds = useMemo(
    () => items.map((w) => w.stock.id).filter((id): id is number => typeof id === "number"),
    [items],
  );

  useEffect(() => {
    if (status !== "authenticated" || !loaded) return;
    if (stockIds.length === 0) {
      setSignals({});
      return;
    }
    let cancelled = false;
    getSignalsByStockIds(stockIds)
      .then((list) => {
        if (cancelled) return;
        setSignals(Object.fromEntries(list.map((s) => [s.stock_id, s])));
      })
      // 신호를 못 불러와도 관심종목 목록 자체는 보여 준다.
      .catch(() => !cancelled && setSignals({}));
    return () => {
      cancelled = true;
    };
  }, [status, loaded, stockIds]);

  // 정렬축이 브리핑과 다르다 — 절대 점수 세기가 아니라 **변화량**이 큰 순.
  // 변화가 없거나 비교 불가인 종목은 아래로 내린다.
  const rows = useMemo(() => {
    if (!signals) return [];
    return items
      .map((w) => ({ code: w.stock.stock_code, item: signals[w.stock.id ?? -1] }))
      .filter((r): r is { code: string; item: SignalListItem } => Boolean(r.code && r.item))
      .sort((a, b) => {
        const da = Math.abs(a.item.change.score_delta ?? -1);
        const db = Math.abs(b.item.change.score_delta ?? -1);
        if (da !== db) return db - da;
        return (b.item.warning_level ?? "").localeCompare(a.item.warning_level ?? "");
      });
  }, [items, signals]);

  const settling = status === "idle" || status === "loading";
  if (settling || (status === "authenticated" && !loaded))
    return <p className="py-16 text-center text-muted">관심종목을 불러오는 중…</p>;

  return (
    <div className="py-10" data-page="watchlist">
      <h1 className="text-[28px] font-extrabold">관심종목</h1>
      <p className="mt-1 text-[13.5px] text-muted">
        담아둔 종목에 무엇이 달라졌는지 먼저 보여드려요. 변화가 큰 순입니다.
      </p>

      {status !== "authenticated" ? (
        <div className="card mt-6 p-6 text-center">
          <p className="text-[14px] text-navy-soft">로그인하면 관심종목을 볼 수 있어요.</p>
          <Link
            href="/login"
            className="brand-grad mt-4 inline-block rounded-full px-6 py-3 text-[15px] font-bold text-white"
          >
            로그인
          </Link>
        </div>
      ) : items.length === 0 ? (
        <div className="card mt-6 p-6 text-center text-[14px] text-muted">
          아직 관심종목이 없습니다. 리포트 화면의 <b>관심종목 추가</b> 버튼으로 담아보세요.
        </div>
      ) : signals === null ? (
        <p className="py-16 text-center text-muted">신호를 불러오는 중…</p>
      ) : rows.length === 0 ? (
        <div className="card mt-6 p-6 text-center text-[14px] text-muted">
          담아둔 종목에 아직 발행된 신호가 없습니다.
        </div>
      ) : (
        <div className="card mt-6 px-2 py-1">
          {rows.map(({ code, item }) => (
            <WatchRow key={code} item={item} code={code} />
          ))}
        </div>
      )}
    </div>
  );
}
