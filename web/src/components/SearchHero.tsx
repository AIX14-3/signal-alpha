"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { searchStocks, type Stock } from "@/lib/apiClient";

export function SearchHero() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Stock[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function onSearch(event: React.FormEvent) {
    event.preventDefault();
    const clean = query.trim();
    if (!clean) return;
    setLoading(true);
    setError(null);
    try {
      const data = await searchStocks(clean);
      setResults(data.items);
      if (data.items.length === 0) setError("검색 결과가 없습니다.");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <section className="converge-bg rounded-[24px] px-6 py-16 text-center sm:py-24">
      <p className="mb-3 text-[13px] font-bold uppercase tracking-[0.12em] text-sky-deep">
        AI 멀티에이전트 투자 신호 분석
      </p>
      <h1 className="mx-auto max-w-[20ch] text-[clamp(28px,5vw,44px)] font-extrabold leading-[1.15] tracking-[-0.02em]">
        데이터가 같은 방향을 가리키는지, 근거와 함께 확인하세요
      </h1>

      <form onSubmit={onSearch} className="mx-auto mt-10 flex max-w-[560px] items-center gap-2">
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="종목명 또는 코드 (예: 삼성전자, 005930)"
          className="card h-13 w-full px-5 py-3 text-[15px] outline-none focus:border-sky"
          aria-label="종목 검색"
        />
        <button
          type="submit"
          disabled={loading}
          className="brand-grad h-13 shrink-0 rounded-full px-6 py-3 text-[15px] font-bold text-white disabled:opacity-60"
        >
          {loading ? "검색 중…" : "분석"}
        </button>
      </form>

      {error && <p className="mt-4 text-[14px] text-muted">{error}</p>}

      {results.length > 0 && (
        <ul className="mx-auto mt-5 max-w-[560px] space-y-2 text-left">
          {results.map((stock) => (
            <li key={stock.id}>
              <button
                type="button"
                onClick={() => router.push(`/report/${stock.stock_code}`)}
                className="card flex w-full items-center justify-between px-5 py-3 hover:border-sky"
              >
                <span className="font-bold">{stock.stock_name}</span>
                <span className="text-[13px] text-muted">
                  {stock.stock_code} · {stock.market ?? "-"}
                </span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
