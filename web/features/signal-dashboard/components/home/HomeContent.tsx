'use client';

import Link from 'next/link';
import {
  Activity,
  Database,
  FileText,
} from 'lucide-react';
import { BrandLogo } from '../layout/BrandLogo';
import { PhoneMockup } from './PhoneMockup';
import { SearchForm } from '../shared/SearchForm';
import { QuickStockCards } from '../shared/QuickStockCards';
import { useDashboard } from '../../context/DashboardContext';

const iconMap = {
  'file-text': FileText,
  database: Database,
  activity: Activity,
} as const;

export function HomeContent({ focusSearch }: { focusSearch?: boolean }) {
  const { selectStock } = useDashboard();

  const newsCards = [
    {
      bg: 'card-orange',
      title: 'HBM3E 양산 가속',
      sub: 'SK하이닉스 Signal 87점 · HIGH',
      light: true,
      onClick: () => selectStock('000660'),
    },
    {
      bg: 'home-dark border border-neutral-800',
      title: '멀티에이전트 v2',
      sub: 'DART · RAG · Alt 통합 런칭',
      light: true,
      href: '/agents',
    },
    {
      bg: 'bg-neutral-100',
      title: 'Signal Journal',
      sub: '나의 판단 기록 · 패턴 인사이트',
      light: false,
      href: '/journal',
    },
  ];

  const featureCards = [
    { icon: 'file-text' as const, title: 'DART Watcher', desc: '전자공시·어닝 서프라이즈 실시간', href: '/agents' },
    { icon: 'database' as const, title: 'Report RAG', desc: '증권사 리포트 교차 검증', href: '/reports' },
    { icon: 'activity' as const, title: 'Alternative', desc: '채용·특허 선행 지표', href: '/stories' },
  ];

  const stats = [
    { n: '4', label: '멀티 에이전트', sub: 'Agents' },
    { n: '0.1s', label: 'SSE 스트리밍', sub: 'Latency' },
    { n: '68%', label: '소스 일치율', sub: 'Consistency', accent: true },
    { n: '3', label: '타깃 종목', sub: 'Live Mock' },
    { n: '24+', label: 'DART 공시', sub: 'Tracked' },
    { n: '±15', label: '급변 알림', sub: 'Journal' },
  ];

  return (
    <div className="relative overflow-hidden">
      <section className="home-hero relative flex min-h-[82vh] flex-col">
        <div className="relative z-10 mx-auto flex w-full max-w-7xl flex-1 items-center px-4 pb-8 pt-10 md:px-8 md:pt-14">
          <div className="animate-fade-in max-w-2xl">
            <BrandLogo variant="hero" />
            <h1 className="text-4xl font-black leading-[1.1] tracking-tight text-white md:text-6xl lg:text-7xl">
              데이터로<br />
              판단의 <span className="text-orange-500">근거</span>를<br />
              만듭니다
            </h1>
            <p className="mt-6 max-w-md text-sm leading-relaxed text-white/75 md:text-base">
              공시 · 리포트 · 대체 데이터를 AI가 교차 검증합니다. 매수/매도가 아닌, 근거 있는
              시그널만 제공합니다.
            </p>
            <div className="mt-8 flex flex-wrap gap-3">
              <Link href="/features" className="btn-orange rounded-full px-8 py-3.5 text-sm font-bold">
                서비스 알아보기
              </Link>
              <Link
                href="/?focus=search"
                className="rounded-full border border-white/50 px-8 py-3.5 text-sm font-bold text-white hover:bg-white/10"
              >
                종목 분석 시작
              </Link>
            </div>
          </div>
        </div>
        <div className="relative z-10 mx-auto w-full max-w-7xl px-4 pb-12 md:px-8">
          <div className="rounded-2xl border border-white/20 bg-white/95 p-4 shadow-2xl backdrop-blur">
            <SearchForm autoFocus={focusSearch} />
          </div>
        </div>
      </section>

      <section className="bg-neutral-100 px-4 py-20 md:py-32">
        <div className="mx-auto max-w-5xl text-center">
          <p className="text-3xl font-black leading-[1.3] tracking-tight text-neutral-900 md:text-5xl md:leading-[1.55] lg:text-6xl lg:leading-[1.2]">
            <span className="text-orange-600">선행 지표</span>로 성장을 읽고,
            <br />
            <span className="text-orange-600">교차 검증</span>으로 신뢰를 쌓으며,
            <br />
            <span className="text-neutral-400">토론</span>으로 합의합니다
          </p>
          <Link
            href="/features"
            className="btn-orange mt-12 inline-block rounded-full px-10 py-4 text-sm font-bold shadow-lg"
          >
            주요 기능 보기 →
          </Link>
        </div>
      </section>

      <section className="bg-white px-4 py-16 md:px-8 md:py-20">
        <div className="mx-auto max-w-7xl">
          <h2 className="mb-2 text-xl font-black text-neutral-900">빠른 시작</h2>
          <p className="mb-8 text-sm text-neutral-500">타깃 3종목 · 클릭 시 시그널 파이프라인 가동</p>
          <div className="grid gap-4 md:grid-cols-3">
            <QuickStockCards />
          </div>
        </div>
      </section>

      <section className="home-dark relative overflow-hidden px-4 py-16 md:py-24">
        <div className="home-glow-ring pointer-events-none absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 opacity-80" />
        <div className="relative z-10 mx-auto flex max-w-7xl flex-col items-center justify-center gap-10 md:flex-row md:gap-20">
          <span className="text-[8rem] font-black leading-none text-white md:text-[12rem]">1</span>
          <div className="text-center md:text-left">
            <div className="mx-auto flex h-28 w-48 items-center justify-center rounded-xl border border-neutral-700 bg-neutral-900 shadow-2xl md:mx-0">
              <span className="text-2xl font-black text-orange-500">87</span>
            </div>
            <p className="mt-8 text-4xl font-black text-orange-500 md:text-5xl">TOP</p>
            <p className="mt-2 text-sm text-neutral-400">SK하이닉스 종합 신뢰도 · 3소스 HIGH 일치</p>
          </div>
        </div>
      </section>

      <section className="home-dark px-4 pb-20">
        <div className="mx-auto grid max-w-6xl grid-cols-2 overflow-hidden rounded-2xl border border-neutral-800 md:grid-cols-3">
          {stats.map((s) => (
            <div
              key={s.label}
              className="border-b border-neutral-800 px-6 py-10 last:border-0 md:border-b-0 md:border-r md:last:border-0"
            >
              <div className={`home-stat-num ${s.accent ? 'text-orange-500' : 'text-white'}`}>
                {s.n}
              </div>
              <p className="mt-3 text-sm font-bold text-white">{s.label}</p>
              <p className="mt-1 text-xs text-orange-500/90">{s.sub}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="home-dark relative px-4 py-24 text-center md:py-32">
        <div className="mb-10 flex justify-center">
          <div className="home-glow-ring flex items-center justify-center">
            <span className="text-2xl font-black tracking-widest text-white md:text-3xl">
              SIGNAL
              <br />
              <span className="text-orange-500">UNIVERSE</span>
            </span>
          </div>
        </div>
        <p className="mx-auto max-w-lg text-sm text-neutral-400">
          DART · Report RAG · Alternative · Debate — 하나의 신뢰 점수로 수렴
        </p>
        <Link href="/agents" className="btn-orange mt-10 inline-block rounded-full px-10 py-4 text-sm font-bold">
          에이전트 허브 →
        </Link>
      </section>

      <section className="home-gradient-panel px-4 py-16 md:px-8 md:py-24">
        <div className="mx-auto grid max-w-7xl items-center gap-12 lg:grid-cols-2">
          <div className="flex justify-center">
            <PhoneMockup />
          </div>
          <div className="space-y-4">
            {featureCards.map((f) => {
              const Icon = iconMap[f.icon];
              return (
                <Link
                  key={f.title}
                  href={f.href}
                  className="card-white block cursor-pointer rounded-2xl p-6 shadow-md transition-shadow hover:shadow-lg"
                >
                  <Icon className="h-8 w-8 text-orange-500" />
                  <h4 className="mt-4 font-bold text-neutral-900">{f.title}</h4>
                  <p className="mt-2 text-xs leading-relaxed text-neutral-600">{f.desc}</p>
                  <span className="mt-4 inline-block text-xs font-bold text-orange-600">
                    더 알아보기 →
                  </span>
                </Link>
              );
            })}
          </div>
        </div>
      </section>

      <section className="bg-white px-4 py-16 md:px-8 md:py-20">
        <div className="mx-auto max-w-7xl">
          <h2 className="mb-10 text-3xl font-black text-neutral-900 md:text-4xl">SIGNAL α NEWS</h2>
          <div className="grid gap-5 md:grid-cols-3">
            {newsCards.map((n) => {
              const tc = n.light ? 'text-white' : 'text-neutral-900';
              const sc = n.light ? 'text-white/80' : 'text-neutral-600';
              const inner = (
                <>
                  <h3 className={`text-xl font-black ${tc}`}>{n.title}</h3>
                  <p className={`mt-2 text-sm ${sc}`}>{n.sub}</p>
                  <span
                    className={`mt-4 text-xs font-bold ${n.light ? 'text-white/90' : 'text-orange-600'}`}
                  >
                    자세히 →
                  </span>
                </>
              );
              if (n.onClick) {
                return (
                  <button
                    key={n.title}
                    type="button"
                    onClick={n.onClick}
                    className={`${n.bg} home-marquee-card flex cursor-pointer flex-col justify-end rounded-2xl p-8 shadow-lg transition-transform hover:scale-[1.02]`}
                  >
                    {inner}
                  </button>
                );
              }
              return (
                <Link
                  key={n.title}
                  href={n.href!}
                  className={`${n.bg} home-marquee-card flex cursor-pointer flex-col justify-end rounded-2xl p-8 shadow-lg transition-transform hover:scale-[1.02]`}
                >
                  {inner}
                </Link>
              );
            })}
          </div>
        </div>
      </section>

      <section className="gradient-orange px-4 py-16 md:px-8 md:py-20">
        <div className="mx-auto max-w-7xl text-center">
          <h2 className="text-3xl font-black leading-tight text-white md:text-5xl">
            지금, 근거 있는 분석을
            <br />
            시작하세요
          </h2>
          <Link
            href="/?focus=search"
            className="mt-8 inline-block rounded-full bg-white px-10 py-4 text-sm font-bold text-orange-700 shadow-lg hover:bg-orange-50"
          >
            무료로 분석하기 →
          </Link>
        </div>
      </section>
    </div>
  );
}
