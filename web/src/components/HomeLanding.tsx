"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { Activity, Database, FileText, Search } from "lucide-react";
import { listStocks, searchStocks, type Stock } from "@/lib/apiClient";

// #335 대시보드 홈 디자인을 메인 앱 홈에 적용 — 파랑→보라(인디고/바이올렛) 재색칠.
// 검색은 실제 API(searchStocks)로 동작하고, 대시보드 전용 링크는 실제 목적지로 재지정한다.

/** 검색어와 가장 잘 맞는 종목 1건: 코드 정확일치 > 종목명 정확일치 > 첫 결과. */
function pickBest(items: Stock[], term: string): Stock {
  const clean = term.trim().toLowerCase();
  return (
    items.find((s) => s.stock_code.toLowerCase() === clean) ??
    items.find((s) => s.stock_name.toLowerCase() === clean) ??
    items[0]
  );
}

function HeroSearch() {
  const router = useRouter();
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      router.push(`/report/${pickBest(data.items, clean).stock_code}`);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  // 한글 IME 조합 중 Enter 가 submit 을 삼키는 문제를 직접 처리.
  function onKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      void search(query);
    }
  }

  return (
    <div id="search" className="scroll-mt-24">
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void search(query);
        }}
        className="relative flex items-center gap-2 rounded-full border border-neutral-200 bg-white py-2 pl-5 pr-2 shadow-sm focus-within:border-violet-500 focus-within:ring-4 focus-within:ring-violet-500/15"
      >
        <Search className="h-5 w-5 shrink-0 text-neutral-400" />
        <input
          ref={inputRef}
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="종목명 또는 코드로 시그널 분석 시작"
          aria-label="종목 검색"
          className="min-w-0 flex-1 bg-transparent text-[15.5px] text-neutral-900 outline-none placeholder:text-neutral-400"
        />
        <button
          type="submit"
          disabled={loading}
          className="btn-indigo shrink-0 rounded-full px-6 py-2.5 text-sm font-bold disabled:opacity-60"
        >
          {loading ? "분석 중…" : "시그널 수집"}
        </button>
      </form>
      {error && <p className="mt-3 px-2 text-sm text-white/80">{error}</p>}
    </div>
  );
}

function QuickStartCards() {
  const [items, setItems] = useState<Stock[]>([]);
  useEffect(() => {
    listStocks(12)
      .then((d) => setItems(d.items.slice(0, 6)))
      .catch(() => setItems([]));
  }, []);

  if (items.length === 0) {
    return <p className="text-sm text-neutral-500">종목을 불러오는 중…</p>;
  }
  return (
    <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {items.map((s) => (
        <Link
          key={s.stock_code}
          href={`/report/${s.stock_code}`}
          className="group rounded-2xl border border-neutral-200 bg-white p-6 shadow-sm transition hover:border-violet-400 hover:shadow-md"
        >
          <div className="flex items-start justify-between">
            <span className="font-mono text-xs text-neutral-500">{s.stock_code}</span>
            <span className="rounded border border-violet-200 bg-violet-50 px-2 py-0.5 text-[10px] font-semibold text-violet-700">
              {s.market ?? "—"}
            </span>
          </div>
          <h3 className="mt-2 text-lg font-black text-neutral-900">{s.stock_name}</h3>
          <p className="mt-1 text-xs text-neutral-500">{s.sector ?? "시그널 리포트"}</p>
          <span className="mt-4 inline-block text-xs font-bold text-violet-600 group-hover:underline">
            리포트 보기 →
          </span>
        </Link>
      ))}
    </div>
  );
}

export function HomeLanding() {
  const featureCards = [
    { Icon: FileText, title: "DART Watcher", desc: "전자공시·어닝 서프라이즈를 실시간으로 추적합니다." },
    { Icon: Database, title: "Report RAG", desc: "증권사 리포트를 교차 검증해 합의를 만듭니다." },
    { Icon: Activity, title: "Alternative", desc: "채용·특허·검색 등 선행 지표를 읽습니다." },
  ];
  const stats = [
    { n: "5", label: "멀티 에이전트", sub: "Agents" },
    { n: "0.1s", label: "실시간 스트리밍", sub: "Latency" },
    { n: "68%", label: "소스 일치율", sub: "Consistency", accent: true },
    { n: "5", label: "데이터 소스", sub: "Sources" },
    { n: "24+", label: "DART 공시", sub: "Tracked" },
    { n: "±15", label: "급변 알림", sub: "Alerts" },
  ];

  return (
    <div className="relative">
      {/* ===== 히어로 ===== */}
      <section className="full-bleed home-hero-indigo relative flex min-h-[80vh] flex-col">
        <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-1 items-center px-6 pb-8 pt-14 md:px-10">
          <div className="animate-fade-in max-w-2xl">
            <div className="mb-6 inline-flex items-center gap-2">
              <span className="brand-mark-indigo grid h-8 w-8 place-items-center rounded-[9px] text-[16px] font-extrabold text-white">
                α
              </span>
              <span className="text-lg font-bold text-white">
                Signal <span className="text-violet-300">α</span>
              </span>
            </div>
            <h1 className="text-4xl font-black leading-[1.1] tracking-tight text-white md:text-6xl lg:text-7xl">
              데이터로
              <br />
              판단의 <span className="text-violet-400">근거</span>를
              <br />
              만듭니다
            </h1>
            <p className="mt-6 max-w-md text-sm leading-relaxed text-white/75 md:text-base">
              공시 · 리포트 · 대체 데이터를 AI가 교차 검증합니다. 매수/매도가 아닌, 근거 있는
              시그널만 제공합니다.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/pricing" className="btn-indigo rounded-full px-8 py-3.5 text-sm font-bold">
                서비스 알아보기
              </Link>
              <a
                href="#search"
                className="rounded-full border border-white/50 px-8 py-3.5 text-sm font-bold text-white hover:bg-white/10"
              >
                종목 분석 시작
              </a>
            </div>
          </div>
        </div>
        <div className="relative z-10 mx-auto w-full max-w-7xl px-6 pb-12 md:px-10">
          <div className="rounded-2xl border border-white/20 bg-white/95 p-4 shadow-2xl backdrop-blur">
            <HeroSearch />
          </div>
        </div>
      </section>

      {/* ===== 스테이트먼트 ===== */}
      <section className="full-bleed bg-neutral-50 px-6 py-20 md:py-28">
        <div className="mx-auto max-w-5xl text-center">
          <p className="text-3xl font-black leading-[1.35] tracking-tight text-neutral-900 md:text-5xl md:leading-[1.4]">
            <span className="text-violet-600">선행 지표</span>로 성장을 읽고,
            <br />
            <span className="text-violet-600">교차 검증</span>으로 신뢰를 쌓으며,
            <br />
            <span className="text-neutral-400">토론</span>으로 합의합니다
          </p>
          <Link
            href="/pricing"
            className="btn-indigo mt-12 inline-block rounded-full px-10 py-4 text-sm font-bold shadow-lg"
          >
            주요 기능 보기 →
          </Link>
        </div>
      </section>

      {/* ===== 빠른 시작 (실데이터) ===== */}
      <section className="px-1 py-16 md:py-20">
        <h2 className="mb-2 text-xl font-black text-neutral-900">빠른 시작</h2>
        <p className="mb-8 text-sm text-neutral-500">관심 종목을 눌러 바로 시그널 리포트를 확인하세요.</p>
        <QuickStartCards />
      </section>

      {/* ===== 다크: 통계 ===== */}
      <section className="full-bleed home-dark-indigo px-6 py-16 md:py-24">
        <div className="mx-auto grid max-w-6xl grid-cols-2 overflow-hidden rounded-2xl border border-white/10 md:grid-cols-3">
          {stats.map((s) => (
            <div
              key={s.label}
              className="border-b border-white/10 px-6 py-10 last:border-0 md:border-b-0 md:border-r md:last:border-0"
            >
              <div className={`home-stat-num ${s.accent ? "text-violet-400" : "text-white"}`}>{s.n}</div>
              <p className="mt-3 text-sm font-bold text-white">{s.label}</p>
              <p className="mt-1 text-xs text-violet-300/90">{s.sub}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ===== 다크: SIGNAL UNIVERSE ===== */}
      <section className="full-bleed home-dark-indigo relative px-6 py-24 text-center md:py-32">
        <div className="mb-10 flex justify-center">
          <div className="home-glow-indigo flex items-center justify-center">
            <span className="text-2xl font-black tracking-widest text-white md:text-3xl">
              SIGNAL
              <br />
              <span className="text-violet-400">UNIVERSE</span>
            </span>
          </div>
        </div>
        <p className="mx-auto max-w-lg text-sm text-neutral-300">
          DART · Report · Alternative · Debate — 하나의 신뢰 점수로 수렴합니다.
        </p>
        <a href="#search" className="btn-indigo mt-10 inline-block rounded-full px-10 py-4 text-sm font-bold">
          지금 분석하기 →
        </a>
      </section>

      {/* ===== 기능 카드 ===== */}
      <section className="full-bleed home-panel-indigo px-6 py-16 md:py-24">
        <div className="mx-auto max-w-6xl">
          <h2 className="mb-10 text-center text-2xl font-black text-neutral-900 md:text-3xl">
            5개 에이전트, 하나의 신뢰 점수
          </h2>
          <div className="grid gap-5 md:grid-cols-3">
            {featureCards.map((f) => (
              <Link
                key={f.title}
                href="/pricing"
                className="block rounded-2xl border border-white/60 bg-white p-6 shadow-md transition hover:shadow-lg"
              >
                <f.Icon className="h-8 w-8 text-violet-600" />
                <h4 className="mt-4 font-bold text-neutral-900">{f.title}</h4>
                <p className="mt-2 text-xs leading-relaxed text-neutral-600">{f.desc}</p>
                <span className="mt-4 inline-block text-xs font-bold text-violet-600">더 알아보기 →</span>
              </Link>
            ))}
          </div>
        </div>
      </section>

      {/* ===== 최종 CTA ===== */}
      <section className="full-bleed gradient-indigo px-6 py-16 md:py-24">
        <div className="mx-auto max-w-7xl text-center">
          <h2 className="text-3xl font-black leading-tight text-white md:text-5xl">
            지금, 근거 있는 분석을
            <br />
            시작하세요
          </h2>
          <a
            href="#search"
            className="mt-8 inline-block rounded-full bg-white px-10 py-4 text-sm font-bold text-violet-700 shadow-lg hover:bg-violet-50"
          >
            무료로 분석하기 →
          </a>
        </div>
      </section>
    </div>
  );
}
