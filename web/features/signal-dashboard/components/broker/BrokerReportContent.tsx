'use client';

import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';
import { useEffect, useState } from 'react';
import {
  AlertTriangle,
  ArrowUpRight,
  Building2,
  ChevronRight,
  FileText,
  TrendingUp,
} from 'lucide-react';
import {
  BROKER_HIGHLIGHTS,
  CONSENSUS_TREND_LABELS,
  EXTENDED_OPINIONS,
  calcConsensusStats,
  parseTargetPrice,
} from '@/lib/signal/broker-mock-data';
import { STORY_ITEMS } from '@/lib/signal/mock-data';
import { fmtWon, getStockByCode } from '@/lib/signal/utils';
import { DirectionBadge } from '../shared/DirectionBadge';

function ratingBadgeClass(rating: string): string {
  if (rating === 'BUY') return 'bg-red-50 text-red-600 border-red-200';
  if (rating === 'SELL') return 'bg-blue-50 text-blue-600 border-blue-200';
  return 'bg-neutral-100 text-neutral-600 border-neutral-200';
}

function RatingDonut({ buy, hold, sell }: { buy: number; hold: number; sell: number }) {
  const total = buy + hold + sell || 1;
  const buyPct = (buy / total) * 100;
  const holdPct = (hold / total) * 100;
  const sellPct = (sell / total) * 100;
  const gradient = `conic-gradient(
    #ef4444 0 ${buyPct}%,
    #a3a3a3 ${buyPct}% ${buyPct + holdPct}%,
    #3b82f6 ${buyPct + holdPct}% 100%
  )`;

  return (
    <div className="flex items-center gap-6">
      <div
        className="relative h-28 w-28 shrink-0 rounded-full"
        style={{ background: gradient }}
      >
        <div className="absolute inset-3 flex flex-col items-center justify-center rounded-full bg-white">
          <span className="text-2xl font-black text-neutral-900">{total}</span>
          <span className="text-[10px] text-neutral-500">리포트</span>
        </div>
      </div>
      <div className="space-y-2 text-sm">
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-red-500" />
          <span className="text-neutral-600">매수</span>
          <span className="font-bold text-neutral-900">{buy}</span>
          <span className="text-xs text-neutral-400">({Math.round(buyPct)}%)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-neutral-400" />
          <span className="text-neutral-600">중립</span>
          <span className="font-bold text-neutral-900">{hold}</span>
          <span className="text-xs text-neutral-400">({Math.round(holdPct)}%)</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="h-2.5 w-2.5 rounded-full bg-blue-500" />
          <span className="text-neutral-600">매도</span>
          <span className="font-bold text-neutral-900">{sell}</span>
          <span className="text-xs text-neutral-400">({Math.round(sellPct)}%)</span>
        </div>
      </div>
    </div>
  );
}

function FirmTargetBars({
  opinions,
  currentPrice,
}: {
  opinions: { firm: string; target: string; rating: string }[];
  currentPrice: number;
}) {
  const sorted = [...opinions].sort(
    (a, b) => parseTargetPrice(b.target) - parseTargetPrice(a.target),
  );
  const max = Math.max(...sorted.map((o) => parseTargetPrice(o.target)), currentPrice);

  return (
    <div className="space-y-3">
      {sorted.map((o) => {
        const target = parseTargetPrice(o.target);
        const pct = Math.round((target / max) * 100);
        const above = target >= currentPrice;
        return (
          <div key={o.firm}>
            <div className="mb-1 flex items-center justify-between text-xs">
              <span className="font-semibold text-neutral-800">{o.firm}</span>
              <span className={`font-mono font-bold ${above ? 'text-red-600' : 'text-blue-600'}`}>
                {o.target}
              </span>
            </div>
            <div className="relative h-2 overflow-hidden rounded-full bg-neutral-100">
              <div
                className={`h-full rounded-full ${above ? 'bg-red-400' : 'bg-blue-400'}`}
                style={{ width: `${pct}%` }}
              />
              <div
                className="absolute top-0 h-full w-0.5 bg-neutral-900/40"
                style={{ left: `${Math.round((currentPrice / max) * 100)}%` }}
                title={`현재가 ${fmtWon(currentPrice)}`}
              />
            </div>
          </div>
        );
      })}
      <p className="text-[10px] text-neutral-400">세로 점선: 현재가 기준</p>
    </div>
  );
}

function TrendChart({ trend }: { trend: number[] }) {
  const max = Math.max(...trend, 1);
  const min = Math.min(...trend);
  const range = max - min || 1;

  return (
    <div className="relative">
      <div className="flex h-32 items-end gap-3">
        {trend.map((v, i) => {
          const h = Math.round(((v - min) / range) * 80) + 20;
          return (
            <div key={CONSENSUS_TREND_LABELS[i]} className="flex flex-1 flex-col items-center gap-2">
              <span className="text-[10px] font-mono font-bold text-neutral-700">
                {(v / 10000).toFixed(0)}만
              </span>
              <div
                className="w-full rounded-t-md bg-gradient-to-t from-orange-500 to-orange-300"
                style={{ height: `${h}%` }}
              />
              <span className="text-[10px] text-neutral-500">{CONSENSUS_TREND_LABELS[i]}</span>
            </div>
          );
        })}
      </div>
      <svg className="pointer-events-none absolute inset-0 h-32 w-full" preserveAspectRatio="none">
        <polyline
          fill="none"
          stroke="#ea580c"
          strokeWidth="2"
          strokeLinecap="round"
          strokeLinejoin="round"
          points={trend
            .map((v, i) => {
              const x = ((i + 0.5) / trend.length) * 100;
              const y = 100 - (((v - min) / range) * 80 + 20);
              return `${x}%,${y}%`;
            })
            .join(' ')}
        />
      </svg>
    </div>
  );
}

export function BrokerReportContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [code, setCode] = useState('000660');

  useEffect(() => {
    const c = searchParams.get('code');
    if (c && EXTENDED_OPINIONS[c]) setCode(c);
  }, [searchParams]);

  const stock = getStockByCode(code);
  const opinions = EXTENDED_OPINIONS[code] ?? stock.report.opinions;
  const highlights = BROKER_HIGHLIGHTS[code] ?? [];
  const currentPrice = stock.quote.price;
  const stats = calcConsensusStats(opinions, currentPrice);
  const trend = stock.report.trend;

  return (
    <div className="animate-fade-in">
      <section className="broker-hero relative overflow-hidden px-4 py-12 md:px-8 md:py-16">
        <div className="relative z-10 mx-auto max-w-7xl">
          <div className="flex flex-wrap items-start justify-between gap-6">
            <div>
              <div className="mb-3 flex items-center gap-2 text-orange-400">
                <Building2 className="h-5 w-5" />
                <span className="text-xs font-bold uppercase tracking-widest">Report RAG</span>
              </div>
              <h1 className="text-3xl font-black text-white md:text-4xl">증권사 리포트</h1>
              <p className="mt-3 max-w-xl text-sm leading-relaxed text-white/70">
                국내 주요 증권사 리서치 리포트를 RAG로 수집·교차 검증합니다. 목표가·투자의견·의견
                충돌을 한 화면에서 확인하세요.
              </p>
            </div>
            <Link
              href={`/main?code=${code}`}
              className="btn-orange flex items-center gap-2 rounded-full px-6 py-3 text-sm font-bold shadow-lg"
            >
              전체 분석 보기
              <ChevronRight className="h-4 w-4" />
            </Link>
          </div>

          <div className="mt-8 flex flex-wrap gap-2">
            {STORY_ITEMS.map((s) => (
              <button
                key={s.code}
                type="button"
                onClick={() => router.push(`/reports?code=${s.code}`)}
                className={`rounded-full border px-4 py-2 text-xs font-bold transition-colors ${
                  code === s.code
                    ? 'border-orange-400 bg-orange-500 text-white'
                    : 'border-white/20 bg-white/10 text-white/80 hover:bg-white/20'
                }`}
              >
                {s.name}
              </button>
            ))}
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-7xl space-y-6 px-4 py-8 md:px-8">
        <div className="card-light -mt-10 relative z-20 rounded-2xl p-6 shadow-lg md:p-8">
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div>
              <div className="flex flex-wrap items-center gap-3">
                <h2 className="text-2xl font-black text-neutral-900">{stock.name}</h2>
                <span className="text-sm text-neutral-500">{stock.code}</span>
                <DirectionBadge direction={stock.report.direction} />
              </div>
              <p className="mt-2 text-sm text-neutral-600">{stock.summary}</p>
            </div>
            <div className="text-right">
              <p className="text-xs text-neutral-500">현재가</p>
              <p className="text-2xl font-black text-neutral-900">{fmtWon(currentPrice)}원</p>
              <p
                className={`text-sm font-bold ${stock.quote.change >= 0 ? 'text-red-600' : 'text-blue-600'}`}
              >
                {stock.quote.change >= 0 ? '+' : ''}
                {fmtWon(stock.quote.change)} ({stock.quote.changePct}%)
              </p>
            </div>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <div className="card-light rounded-xl p-5">
            <p className="text-xs font-bold uppercase text-neutral-500">RAG 점수</p>
            <p className="mt-2 text-3xl font-black text-orange-600">{stock.report.score}</p>
            <p className="mt-1 text-xs text-neutral-500">리포트 신뢰도</p>
          </div>
          <div className="card-light rounded-xl p-5">
            <p className="text-xs font-bold uppercase text-neutral-500">컨센서스 목표가</p>
            <p className="mt-2 text-3xl font-black text-neutral-900">{fmtWon(stats.avgTarget)}</p>
            <p className="mt-1 text-xs text-neutral-500">{stats.total}개 증권사 평균</p>
          </div>
          <div className="card-light rounded-xl p-5">
            <p className="text-xs font-bold uppercase text-neutral-500">업사이드</p>
            <p
              className={`mt-2 flex items-center gap-1 text-3xl font-black ${
                stats.upsidePct >= 0 ? 'text-red-600' : 'text-blue-600'
              }`}
            >
              {stats.upsidePct >= 0 ? '+' : ''}
              {stats.upsidePct}%
              <TrendingUp className="h-5 w-5" />
            </p>
            <p className="mt-1 text-xs text-neutral-500">현재가 대비</p>
          </div>
          <div className="card-light rounded-xl p-5">
            <p className="text-xs font-bold uppercase text-neutral-500">의견 일치도</p>
            {stock.report.conflict ? (
              <div className="mt-2 flex items-center gap-2">
                <AlertTriangle className="h-6 w-6 text-amber-500" />
                <span className="text-lg font-black text-amber-700">충돌 감지</span>
              </div>
            ) : (
              <div className="mt-2 flex items-center gap-2">
                <span className="text-lg font-black text-green-700">안정</span>
              </div>
            )}
            <p className="mt-1 text-xs text-neutral-500">
              {stock.report.conflict ? '매수·매도 의견 분산' : '컨센서스 방향 일치'}
            </p>
          </div>
        </div>

        <div className="grid gap-6 lg:grid-cols-2">
          <div className="card-light rounded-2xl p-6 shadow-sm">
            <h3 className="mb-6 flex items-center gap-2 text-sm font-black text-neutral-900">
              <Building2 className="h-4 w-4 text-orange-500" />
              투자의견 분포
            </h3>
            <RatingDonut buy={stats.buy} hold={stats.hold} sell={stats.sell} />
          </div>

          <div className="card-light rounded-2xl p-6 shadow-sm">
            <h3 className="mb-6 text-sm font-black text-neutral-900">목표가 추이 (3개월)</h3>
            <TrendChart trend={trend} />
          </div>
        </div>

        <div className="card-light rounded-2xl p-6 shadow-sm">
          <h3 className="mb-6 text-sm font-black text-neutral-900">증권사별 목표가</h3>
          <FirmTargetBars opinions={opinions} currentPrice={currentPrice} />
        </div>

        <div className="card-light overflow-hidden rounded-2xl shadow-sm">
          <div className="border-b border-neutral-100 px-6 py-4">
            <h3 className="text-sm font-black text-neutral-900">증권사 의견 전체</h3>
            <p className="mt-1 text-xs text-neutral-500">최근 90일 내 발행 리포트 기준</p>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-left text-sm">
              <thead className="bg-neutral-50">
                <tr className="text-[10px] uppercase text-neutral-500">
                  <th className="px-6 py-3 font-bold">증권사</th>
                  <th className="px-6 py-3 font-bold">투자의견</th>
                  <th className="px-6 py-3 font-bold">목표가</th>
                  <th className="px-6 py-3 font-bold">업사이드</th>
                  <th className="px-6 py-3 font-bold">발행일</th>
                </tr>
              </thead>
              <tbody>
                {opinions.map((o) => {
                  const target = parseTargetPrice(o.target);
                  const upside = Number((((target - currentPrice) / currentPrice) * 100).toFixed(1));
                  return (
                    <tr key={o.firm} className="border-t border-neutral-100 hover:bg-orange-50/30">
                      <td className="px-6 py-4 font-semibold text-neutral-900">{o.firm}</td>
                      <td className="px-6 py-4">
                        <span
                          className={`rounded border px-2.5 py-1 text-xs font-bold ${ratingBadgeClass(o.rating)}`}
                        >
                          {o.rating}
                        </span>
                      </td>
                      <td className="px-6 py-4 font-mono font-bold text-neutral-800">{o.target}</td>
                      <td
                        className={`px-6 py-4 font-bold ${upside >= 0 ? 'text-red-600' : 'text-blue-600'}`}
                      >
                        {upside >= 0 ? '+' : ''}
                        {upside}%
                      </td>
                      <td className="px-6 py-4 text-xs text-neutral-500">{o.date}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>

        {highlights.length > 0 && (
          <div>
            <h3 className="mb-4 flex items-center gap-2 text-lg font-black text-neutral-900">
              <FileText className="h-5 w-5 text-orange-500" />
              최근 리포트 하이라이트
            </h3>
            <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
              {highlights.map((h) => (
                <article
                  key={h.id}
                  className="card-light group rounded-2xl p-5 shadow-sm transition-shadow hover:shadow-md"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="text-xs font-bold text-orange-600">{h.firm}</span>
                    <span
                      className={`rounded border px-2 py-0.5 text-[10px] font-bold ${ratingBadgeClass(h.rating)}`}
                    >
                      {h.rating}
                    </span>
                  </div>
                  <h4 className="mt-3 font-bold leading-snug text-neutral-900 group-hover:text-orange-600">
                    {h.title}
                  </h4>
                  <p className="mt-2 line-clamp-3 text-xs leading-relaxed text-neutral-600">
                    {h.excerpt}
                  </p>
                  <div className="mt-4 flex items-center justify-between text-[10px] text-neutral-500">
                    <span>
                      {h.analyst} · {h.date}
                    </span>
                    <span className="font-mono font-bold text-neutral-700">{h.target}원</span>
                  </div>
                </article>
              ))}
            </div>
          </div>
        )}

        <div className="gradient-orange rounded-2xl px-6 py-10 text-center md:px-10">
          <h3 className="text-xl font-black text-white md:text-2xl">
            리포트만으로는 부족합니다
          </h3>
          <p className="mx-auto mt-3 max-w-lg text-sm text-white/80">
            공시·대안 데이터·AI 토론까지 교차 검증한 종합 시그널을 확인하세요.
          </p>
          <div className="mt-6 flex flex-wrap justify-center gap-3">
            <Link
              href={`/main?code=${code}`}
              className="inline-flex items-center gap-2 rounded-full bg-white px-8 py-3 text-sm font-bold text-orange-700 shadow-lg hover:bg-orange-50"
            >
              종합 분석 대시보드
              <ArrowUpRight className="h-4 w-4" />
            </Link>
            <Link
              href="/agents"
              className="rounded-full border border-white/40 px-8 py-3 text-sm font-bold text-white hover:bg-white/10"
            >
              Report RAG 에이전트
            </Link>
          </div>
        </div>
      </div>
    </div>
  );
}
