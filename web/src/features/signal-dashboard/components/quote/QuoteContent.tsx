'use client';

import Link from 'next/link';
import { useEffect } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { getChartTimeAxisLabel } from '@/lib/signal/chart-utils';
import { MARKET_INDICES, STORY_ITEMS } from '@/lib/signal/mock-data';
import { fmtWon, getStockByCode } from '@/lib/signal/utils';
import type { ChartPeriod } from '@/types/chart';
import { useStockChart } from '../../hooks/useStockChart';
import { useDashboard } from '../../context/DashboardContext';
import { ChartLegend } from '../chart/ChartLegend';
import { ChartPeriodTabs } from '../chart/ChartPeriodTabs';
import { StockChart } from '../chart/StockChart';
import { Sparkline } from '../shared/Sparkline';
import { DirectionBadge } from '../shared/DirectionBadge';

export function QuoteContent() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const {
    quoteCode,
    quotePeriod,
    selectedStock,
    setQuoteCode,
    setQuotePeriod,
    selectStock,
    saveToJournal,
  } = useDashboard();

  useEffect(() => {
    const code = searchParams.get('code');
    if (code) setQuoteCode(code);
  }, [searchParams, setQuoteCode]);

  const stock = getStockByCode(quoteCode);
  const q = stock.quote;
  const up = q.change >= 0;
  const chgCls = up ? 'text-red-600' : 'text-blue-600';
  const signalScore = stock.score;
  const { bars, markers } = useStockChart(quoteCode, quotePeriod as ChartPeriod);
  const timeAxis = getChartTimeAxisLabel(quotePeriod as ChartPeriod);

  const srcRows = [
    { l: 'DART 공시', s: stock.dart.score },
    { l: 'Report RAG', s: stock.report.score },
    { l: 'Alternative', s: stock.alt.score },
    { l: '소스 일치성', s: stock.consistency },
  ];

  return (
    <div className="animate-fade-in mx-auto max-w-7xl space-y-6 px-4 py-8 md:px-6">
      <div className="card-light rounded-2xl p-6 shadow-sm md:p-8">
        <div className="flex flex-wrap justify-between gap-6">
          <div className="min-w-[280px] flex-1">
            <div className="mb-4 flex flex-wrap gap-2">
              {STORY_ITEMS.map((s) => (
                <button
                  key={s.code}
                  type="button"
                  onClick={() => router.push(`/dashboard/quote?code=${s.code}`)}
                  className={`rounded-full border px-3 py-1.5 text-xs font-bold ${
                    quoteCode === s.code
                      ? 'card-orange border-transparent text-white'
                      : 'btn-outline'
                  }`}
                >
                  {s.name}
                </button>
              ))}
            </div>
            <h1 className="text-2xl font-black text-neutral-900 md:text-3xl">{stock.name}</h1>
            <p className="mt-1 text-xs text-neutral-500">
              KRX · {stock.code} · {q.nameEn}
            </p>
            <div className="mt-4 flex flex-wrap items-baseline gap-3">
              <span className="text-3xl font-black md:text-4xl">
                {fmtWon(q.price)}
                <span className="text-lg font-normal text-neutral-500">원</span>
              </span>
              <span className={`text-lg font-bold ${chgCls}`}>
                {up ? '+' : ''}
                {fmtWon(q.change)}원 ({up ? '+' : ''}
                {q.changePct}%)
              </span>
            </div>
            <p className="mt-3 text-[10px] text-neutral-400">
              {q.asOf} · 목업 시세(2026-06-02 KRX 종가 기준) · DART · 리포트 · Alt
            </p>
          </div>
          <div className="flex flex-col items-end gap-3">
            <DirectionBadge direction={stock.direction} className="px-4 py-2 text-xs" />
            <div className="flex flex-wrap justify-end gap-2">
              <button type="button" className="btn-outline rounded-full px-4 py-2 text-xs font-bold">
                관심 종목 추가
              </button>
              {selectedStock ? (
                <Link href="/dashboard/main" className="btn-outline rounded-full px-4 py-2 text-xs font-bold">
                  근거 데이터 보기
                </Link>
              ) : (
                <button
                  type="button"
                  onClick={() => selectStock(stock.code)}
                  className="btn-outline rounded-full px-4 py-2 text-xs font-bold"
                >
                  시그널 분석
                </button>
              )}
              <button
                type="button"
                onClick={() => saveToJournal(stock)}
                className="btn-orange rounded-full px-4 py-2 text-xs font-bold"
              >
                내 판단 기록하기
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {[
          { label: '현재가', val: `${fmtWon(q.price)} KRW`, sub: '실시간' },
          {
            label: '전일 대비',
            val: `${up ? '+' : ''}${fmtWon(q.change)} (${up ? '+' : ''}${q.changePct}%)`,
            sub: up ? 'Up' : 'Down',
          },
          { label: 'Signal Score', val: `${signalScore} / 100`, sub: 'Positive bias' },
          { label: 'Confidence', val: stock.consistency, sub: 'Evidence reliability' },
          { label: '데이터 방향 일치도', val: q.sourcesAgree, sub: '소스 동의' },
        ].map((kpi) => (
          <div key={kpi.label} className="card-light rounded-xl p-4 shadow-sm">
            <span className="text-[10px] uppercase text-neutral-500">{kpi.label}</span>
            <p className="mt-1 text-lg font-black text-neutral-900">{kpi.val}</p>
            {kpi.sub && <p className="mt-1 text-[10px] text-neutral-500">{kpi.sub}</p>}
          </div>
        ))}
      </div>

      <div className="grid gap-6 lg:grid-cols-12">
        <div className="space-y-4 lg:col-span-8">
          <div className="card-light rounded-2xl p-5 shadow-sm">
            <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 className="font-bold text-neutral-900">{stock.name} 가격 흐름</h2>
              <ChartPeriodTabs
                period={quotePeriod as ChartPeriod}
                onChange={(p) => setQuotePeriod(p)}
              />
            </div>
            <StockChart bars={bars} markers={markers} />
            <div className="mt-2 flex justify-between px-2 text-[10px] text-neutral-400">
              <span>{timeAxis.start}</span>
              <span>{timeAxis.mid}</span>
              <span>{timeAxis.end}</span>
            </div>
            <ChartLegend markerCount={markers.length} />
          </div>
          <div className="card-light rounded-2xl p-5 shadow-sm">
            <h3 className="mb-3 text-sm font-bold text-neutral-900">종목 정보</h3>
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
              {[
                ['시가', fmtWon(q.open)],
                ['고가', fmtWon(q.high)],
                ['저가', fmtWon(q.low)],
                ['거래량', q.volume],
                ['시가총액', q.marketCap],
                ['52주 최고', fmtWon(q.week52High)],
                ['52주 최저', fmtWon(q.week52Low)],
                ['PER', String(q.per)],
              ].map(([l, v]) => (
                <div key={l} className="rounded-lg border border-neutral-100 p-3">
                  <span className="block text-[10px] text-neutral-500">{l}</span>
                  <span className="text-sm font-bold text-neutral-900">{v}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="lg:col-span-4">
          <div className="card-light sticky top-24 rounded-2xl p-6 shadow-sm">
            <h3 className="font-bold text-neutral-900">근거 기반 종합 신호</h3>
            <p className="mt-1 text-[10px] text-neutral-500">Composite Signal · Mock</p>
            <div className="my-6 flex justify-center">
              <div
                className="donut-ring relative flex h-40 w-40 items-center justify-center rounded-full"
                style={{ '--pct': `${signalScore}%` } as React.CSSProperties}
              >
                <div className="flex h-28 w-28 flex-col items-center justify-center rounded-full bg-white shadow-inner">
                  <span className="text-3xl font-black text-blue-600">{signalScore}</span>
                  <span className="text-[10px] text-neutral-500">Signal Score</span>
                </div>
              </div>
            </div>
            <div className="mb-4">
              {srcRows.map((r) => (
                <div
                  key={r.l}
                  className="flex justify-between border-b border-neutral-100 py-2 text-sm last:border-0"
                >
                  <span className="text-neutral-600">{r.l}</span>
                  <span className="font-bold">
                    {typeof r.s === 'number' ? `${r.s}점` : r.s}
                  </span>
                </div>
              ))}
            </div>
            <p className="border-t border-neutral-100 pt-4 text-xs leading-relaxed text-neutral-600">
              {stock.summary}
            </p>
            <p className="mt-3 text-[10px] text-neutral-400">
              ※ 투자 권유가 아닌 정보 제공 목적입니다.
            </p>
          </div>
        </div>
      </div>

      <div className="flex flex-wrap gap-3 overflow-x-auto pb-2">
        {MARKET_INDICES.map((m) => (
          <div key={m.name} className="card-light min-w-[140px] flex-1 rounded-xl p-4 shadow-sm">
            <span className="text-[10px] font-bold text-neutral-500">{m.name}</span>
            <p className="mt-1 text-sm font-black">{m.value}</p>
            <p className={`text-xs font-bold ${m.up ? 'text-green-600' : 'text-red-600'}`}>
              {m.up ? '+' : ''}
              {m.chg}%
            </p>
            <Sparkline values={m.spark} up={m.up} />
          </div>
        ))}
      </div>
    </div>
  );
}
