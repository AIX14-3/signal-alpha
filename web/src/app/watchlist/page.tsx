"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useAuthStore } from "@/stores/authStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

export default function WatchlistPage() {
  const status = useAuthStore((s) => s.status);
  const items = useWatchlistStore((s) => s.items);
  const loaded = useWatchlistStore((s) => s.loaded);
  const ensureLoaded = useWatchlistStore((s) => s.ensureLoaded);

  useEffect(() => {
    if (status === "authenticated") void ensureLoaded();
  }, [status, ensureLoaded]);

  const settling = status === "idle" || status === "loading";
  if (settling || (status === "authenticated" && !loaded))
    return <p className="py-16 text-center text-muted">관심종목을 불러오는 중…</p>;

  return (
    <div className="py-10" data-page="watchlist">
      <h1 className="text-[28px] font-extrabold">관심종목</h1>
      <p className="mt-1 text-[13.5px] text-muted">담아둔 종목의 리포트로 바로 이동할 수 있어요.</p>

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
      ) : (
        <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((w) => (
            <Link
              key={w.stock.stock_code}
              href={`/report/${encodeURIComponent(w.stock.stock_code)}`}
              className="card block p-5 transition hover:shadow-lg"
            >
              <div className="text-[12px] text-muted">
                {w.stock.stock_code} · {w.stock.market ?? "—"} · {w.stock.sector ?? "—"}
              </div>
              <div className="mt-1 text-[18px] font-bold">{w.stock.stock_name}</div>
              <div className="mt-3 text-[13px] font-semibold text-sky-deep">리포트 보기 →</div>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
