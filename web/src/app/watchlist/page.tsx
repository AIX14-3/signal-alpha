"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { getSignalsByStockIds, type SignalListItem } from "@/lib/apiClient";
import { directionLabel, scoreText, sourceLabel, warningLevelLabel } from "@/lib/format";
import { useAuthStore } from "@/stores/authStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 관심종목 = **추적** 화면. 브리핑(/brief)이 모르는 종목을 훑는 "발견"이라면, 여기는 이미 아는
// 종목에 무엇이 달라졌는지 본다. 레이아웃은 브리핑과 같은 카드(읽기 편함)를 쓰되, 두 가지가 다르다.
//   1) 정렬축 — 절대 점수 세기가 아니라 **변화량(Δ)** 이 큰 순.
//   2) 카드 안에 **소스별 막대 그래프** — 브리핑 카드에 없는 정보.

const SOURCE_KEYS = ["dart", "price", "report", "datalab", "patent", "hiring"] as const;

/** 점수의 중립값. 0이 아니라 50이 기준이므로 막대도 여기서 갈라진다. */
const NEUTRAL_SCORE = 50;
/** 이 폭 안이면 중립으로 본다. 50.0 을 빨간(긍정) 막대로 그리면 없는 방향이 생긴다. */
const NEUTRAL_BAND = 0.5;

function tone(t: "up" | "down" | "flat"): string {
  return t === "up" ? "up" : t === "down" ? "down" : "flat";
}

/** 변화량 표기. null 은 "변화 없음"이 아니라 비교 불가다 — 다르게 보여 준다. */
function DeltaBadge({ change }: { change: SignalListItem["change"] }) {
  const delta = change.score_delta;
  if (delta === null) {
    const reason = change.previous_signal_date
      ? "직전 발행이 오래되어 비교할 수 없습니다"
      : "비교할 이전 신호가 없습니다";
    return (
      <span className="text-[12px] text-muted" title={reason}>
        비교 불가
      </span>
    );
  }
  if (delta === 0) return <span className="text-[12px] text-muted">변화 없음</span>;
  // 등락 색은 한국 관례(상승 빨강 / 하락 파랑).
  const up = delta > 0;
  return (
    <span
      className={`text-[14px] font-bold ${up ? "text-rose-600" : "text-blue-600"}`}
      title={`직전 발행(${change.previous_signal_date}) 대비`}
    >
      {up ? "▲" : "▼"} {Math.abs(delta).toFixed(1)}
    </span>
  );
}

/** 소스별 점수 막대. 중립 50을 가운데 선으로 두고 위/아래로 자란다(0 기준이 아니다). */
function SourceBars({ item }: { item: SignalListItem }) {
  const sources = item.score_breakdown?.sources ?? {};
  return (
    <div className="mt-3" role="img" aria-label="소스별 점수 분포">
      <div className="flex items-stretch gap-1.5" style={{ height: 46 }}>
        {SOURCE_KEYS.map((key) => {
          const src = sources[key];
          const score = src?.score ?? null;
          const missing = !src || score === null || src.data_status === "missing";
          const offset = missing ? 0 : score - NEUTRAL_SCORE;
          const neutral = !missing && Math.abs(offset) < NEUTRAL_BAND;
          // 중립선 위/아래로 뻗는 길이(최대 절반). 50 → 0, 100 → 절반, 0 → 절반.
          const magnitude = Math.min(50, Math.abs(offset)) / 50;
          const above = offset > 0;
          return (
            <div
              key={key}
              className="relative flex-1"
              title={
                missing
                  ? `${sourceLabel(key)} · 데이터 없음`
                  : `${sourceLabel(key)} ${scoreText(score)}점${neutral ? " (중립)" : ""}`
              }
            >
              {/* 중립선 */}
              <span className="absolute inset-x-0 top-1/2 h-px bg-line" aria-hidden="true" />
              {missing ? (
                <span className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full border border-dashed border-line" />
              ) : neutral ? (
                // 중립은 방향색을 쓰지 않는다 — 가운데 회색 눈금.
                <span className="absolute inset-x-0 top-1/2 h-1 -translate-y-1/2 rounded-full bg-muted/40" />
              ) : (
                <span
                  className={`absolute inset-x-0 rounded-[2px] ${above ? "bg-rose-400" : "bg-blue-400"}`}
                  style={
                    above
                      ? { bottom: "50%", height: `${Math.max(6, magnitude * 50)}%` }
                      : { top: "50%", height: `${Math.max(6, magnitude * 50)}%` }
                  }
                />
              )}
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex gap-1.5">
        {SOURCE_KEYS.map((key) => (
          <span key={key} className="flex-1 truncate text-center text-[10px] text-muted">
            {sourceLabel(key)}
          </span>
        ))}
      </div>
    </div>
  );
}

function WatchCard({ item, code }: { item: SignalListItem; code: string }) {
  const dir = directionLabel(item.direction);
  const warn = warningLevelLabel(item.warning_level);
  const hasScore = item.score != null;

  return (
    <Link
      href={`/report/${encodeURIComponent(code)}`}
      className="glass hover-lift block p-5"
      data-flow="watchlist-card"
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
            <span className="text-[32px] font-extrabold leading-none">{scoreText(item.score)}</span>
            <span className="text-[13px] font-semibold text-muted">/100</span>
          </>
        ) : (
          <span className="text-[15px] font-bold text-muted">점수 없음</span>
        )}
        <span className="ml-auto">
          <DeltaBadge change={item.change} />
        </span>
      </div>

      {warn.severity !== "normal" && (
        <div className="mt-2">
          <span className="pill down" style={{ padding: "3px 9px", fontSize: 11.5 }}>
            {warn.label}
          </span>
        </div>
      )}

      <SourceBars item={item} />

      <div className="mt-3 text-[13px] font-semibold text-sky-deep">리포트 보기 →</div>
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
  const cards = useMemo(() => {
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
    <div className="relative py-8" data-page="watchlist">
      <div className="report-aura pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />

      <header className="mb-6">
        <h1 className="text-[30px] font-extrabold">관심종목</h1>
        <p className="mt-1 text-[13.5px] text-muted">
          담아둔 종목에 무엇이 달라졌는지 먼저 보여드려요. 직전 발행 대비 변화가 큰 순입니다.
        </p>
      </header>

      {status !== "authenticated" ? (
        <div className="glass p-6 text-center">
          <p className="text-[14px] text-navy-soft">로그인하면 관심종목을 볼 수 있어요.</p>
          <Link
            href="/login"
            className="brand-grad mt-4 inline-block rounded-full px-6 py-3 text-[15px] font-bold text-white"
          >
            로그인
          </Link>
        </div>
      ) : items.length === 0 ? (
        <div className="glass p-8 text-center text-[14px] text-muted">
          아직 관심종목이 없습니다. 리포트 화면의 <b>관심종목 추가</b> 버튼으로 담아보세요.
        </div>
      ) : signals === null ? (
        <p className="py-16 text-center text-muted">신호를 불러오는 중…</p>
      ) : cards.length === 0 ? (
        <div className="glass p-8 text-center text-[14px] text-muted">
          담아둔 종목에 아직 발행된 신호가 없습니다.
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {cards.map(({ code, item }) => (
            <WatchCard key={code} item={item} code={code} />
          ))}
        </div>
      )}
    </div>
  );
}
