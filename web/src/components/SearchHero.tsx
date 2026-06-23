"use client";

import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { listStocks, searchStocks, type Stock } from "@/lib/apiClient";

const FALLBACK_PLACEHOLDER = "종목명 또는 코드 입력";

/** 실제 종목명을 받아와 타이핑되듯 흘러가는 placeholder 훅. */
function useTypingPlaceholder(active: boolean): string {
  const [names, setNames] = useState<string[]>([]);
  const [text, setText] = useState("");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    listStocks(100)
      .then((data) => setNames(data.items.map((s) => s.stock_name).filter(Boolean)))
      .catch(() => setNames([]));
  }, []);

  useEffect(() => {
    if (!active || names.length === 0) {
      setText("");
      return;
    }
    let nameIndex = 0;
    let charIndex = 0;
    let deleting = false;

    function tick() {
      const current = names[nameIndex % names.length];
      if (!deleting) {
        charIndex += 1;
        setText(current.slice(0, charIndex));
        if (charIndex >= current.length) {
          deleting = true;
          timer.current = setTimeout(tick, 1400); // 완성 후 잠시 머무름
          return;
        }
        timer.current = setTimeout(tick, 110);
      } else {
        charIndex -= 1;
        setText(current.slice(0, Math.max(0, charIndex)));
        if (charIndex <= 0) {
          deleting = false;
          nameIndex += 1;
          timer.current = setTimeout(tick, 350); // 다음 종목 전 간격
          return;
        }
        timer.current = setTimeout(tick, 55);
      }
    }

    timer.current = setTimeout(tick, 300);
    return () => {
      if (timer.current) clearTimeout(timer.current);
    };
  }, [active, names]);

  if (!active) return FALLBACK_PLACEHOLDER;
  return text ? `${text}` : FALLBACK_PLACEHOLDER;
}

/** 검색어와 가장 잘 맞는 종목 1건 선택: 코드 정확일치 > 종목명 정확일치 > 첫 결과. */
function pickBest(items: Stock[], term: string): Stock {
  const clean = term.trim().toLowerCase();
  return (
    items.find((s) => s.stock_code.toLowerCase() === clean) ??
    items.find((s) => s.stock_name.toLowerCase() === clean) ??
    items[0]
  );
}

export function SearchHero() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // 입력값이 없을 때만 안내(타이핑) placeholder 노출. 사용자가 글자를 적으면 사라짐.
  const placeholder = useTypingPlaceholder(query.length === 0);

  async function search(term: string) {
    const clean = term.trim();
    if (!clean) return;
    setLoading(true);
    setError(null);
    try {
      const data = await searchStocks(clean);
      if (data.items.length === 0) {
        setError("검색 결과가 없습니다.");
        return;
      }
      // 엔터/분석 시 가장 잘 맞는 종목 리포트로 바로 이동.
      router.push(`/report/${pickBest(data.items, clean).stock_code}`);
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

  // 한글 IME: 조합 중 Enter는 form submit을 삼켜 두 번 눌러야 하는 문제를
  // 직접 처리한다. query에는 이미 입력값이 반영돼 있어 첫 Enter로 검색된다.
  function onKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      void search(query);
    }
  }

  return (
    <section className="relative z-10 px-2 pb-14 pt-24 text-center sm:pt-28">
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
          onKeyDown={onKeyDown}
          placeholder={placeholder}
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

      {error && <p className="mt-5 text-[14px] text-muted">{error}</p>}
    </section>
  );
}
