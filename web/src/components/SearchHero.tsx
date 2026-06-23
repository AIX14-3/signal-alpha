"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { searchStocks, type Stock } from "@/lib/apiClient";

const SAMPLES = ["삼성전자", "SK하이닉스", "NAVER", "카카오", "현대차"];

export function SearchHero() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<Stock[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function search(term: string) {
    const clean = term.trim();
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

  function onSubmit(event: React.FormEvent) {
    event.preventDefault();
    void search(query);
  }

  function pick(term: string) {
    setQuery(term);
    void search(term);
  }

  return (
    <section className="px-2 pb-14 pt-24 text-center sm:pt-28">
      <span className="pill mb-7 border border-line bg-surface-2 px-[13px] py-1.5 text-[13px] font-semibold text-navy-soft">
        <span className="live-dot" /> 멀티에이전트가 실시간으로 분석합니다
      </span>

      <h1 className="mx-auto mb-5 max-w-[14ch] text-[clamp(38px,6vw,64px)] font-extrabold leading-[1.04] tracking-[-0.03em]">
        종목 하나면,
        <br />
        <span className="bg-gradient-to-r from-sky to-green bg-clip-text text-transparent">
          AI 리서치 한 장
        </span>
        으로
      </h1>
      <p className="mx-auto mb-10 max-w-[48ch] text-[clamp(16px,2vw,19px)] text-muted">
        공시·재무·뉴스·수급·시계열을 5개 에이전트가 동시에 분석해 투자 신호로 정리합니다.
      </p>

      <form
        onSubmit={onSubmit}
        className="mx-auto flex max-w-[560px] items-center gap-2.5 rounded-full border border-line bg-surface py-2 pl-[22px] pr-2 shadow-[var(--shadow-card)] transition focus-within:border-sky focus-within:ring-4 focus-within:ring-sky/15"
      >
        <svg
          width="20"
          height="20"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2"
          strokeLinecap="round"
          className="shrink-0 text-muted"
        >
          <circle cx="11" cy="11" r="7" />
          <path d="m21 21-4.3-4.3" />
        </svg>
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="종목명 또는 코드 입력 (예: 삼성전자)"
          aria-label="종목 검색"
          className="min-w-0 flex-1 bg-transparent text-[16.5px] outline-none placeholder:text-muted"
        />
        <button
          type="submit"
          disabled={loading}
          className="shrink-0 rounded-full bg-navy px-[26px] py-[13px] text-[15px] font-semibold text-white transition hover:-translate-y-px disabled:opacity-60"
        >
          {loading ? "분석 중…" : "분석"}
        </button>
      </form>

      <div className="mt-[18px] flex flex-wrap justify-center gap-2">
        {SAMPLES.map((sample) => (
          <button
            key={sample}
            type="button"
            onClick={() => pick(sample)}
            className="rounded-full border border-line bg-surface px-3.5 py-[7px] text-[13.5px] font-medium text-navy-soft transition hover:-translate-y-px hover:border-sky hover:text-sky-deep"
          >
            {sample}
          </button>
        ))}
      </div>

      {error && <p className="mt-5 text-[14px] text-muted">{error}</p>}

      {results.length > 0 && (
        <ul className="mx-auto mt-6 max-w-[560px] space-y-2 text-left">
          {results.map((stock) => (
            <li key={stock.id}>
              <button
                type="button"
                onClick={() => router.push(`/report/${stock.stock_code}`)}
                className="card flex w-full items-center justify-between px-5 py-3 transition hover:border-sky"
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
