'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useEffect } from 'react';
import {
  AlertTriangle,
  ArrowLeft,
  ChevronRight,
  Database,
  FileText,
  Sliders,
  TrendingUp,
} from 'lucide-react';
import { dirChipClass, surprisePct } from '@/lib/signal/utils';
import type { DrilldownKey } from '@/types/signal';
import { useDashboard } from '../../context/DashboardContext';

export function MainResultContent() {
  const router = useRouter();
  const { selectedStock, setActiveDrilldown, saveToJournal, reanalyze } = useDashboard();

  useEffect(() => {
    if (!selectedStock) router.replace('/');
  }, [selectedStock, router]);

  if (!selectedStock) return null;

  const stock = selectedStock;
  const pct = surprisePct(stock);
  const hitl = stock.score < 60;

  const agentCard = (
    key: DrilldownKey,
    Icon: React.ComponentType<{ className?: string }>,
    title: string,
    score: number,
    desc: string,
  ) => (
    <button
      key={key}
      type="button"
      onClick={() => setActiveDrilldown(key)}
      className="card-light group flex cursor-pointer justify-between rounded-xl p-5 shadow-sm hover:border-orange-400"
    >
      <div>
        <div className="flex items-center gap-2">
          <Icon className="h-4 w-4 text-orange-500" />
          <span className="font-bold text-neutral-900 group-hover:text-orange-600">{title}</span>
          <span
            className={`rounded border px-2 py-0.5 text-[10px] ${dirChipClass(score >= 70 ? 'POSITIVE' : 'MIXED')}`}
          >
            {score}점
          </span>
        </div>
        <p className="mt-2 text-xs text-neutral-500">{desc}</p>
      </div>
      <ChevronRight className="h-5 w-5 text-neutral-400" />
    </button>
  );

  return (
    <div className="animate-fade-in mx-auto max-w-7xl space-y-8 px-4 py-10">
      {hitl && (
        <div className="card-light flex gap-3 rounded-xl border-amber-300 bg-amber-50 p-4 text-sm">
          <AlertTriangle className="h-5 w-5 shrink-0 text-amber-600" />
          <p className="text-neutral-600">
            신뢰 {stock.score}점 —{' '}
            <button
              type="button"
              onClick={() => setActiveDrilldown('dart')}
              className="cursor-pointer text-orange-600 underline"
            >
              원본 실사
            </button>{' '}
            권장
          </p>
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-neutral-200 pb-6">
        <div className="flex items-center gap-3">
          <Link
            href="/dashboard"
            className="rounded-full border border-neutral-300 p-2 text-neutral-700 hover:border-orange-400"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <h2 className="text-2xl font-black text-neutral-900">
            {stock.name}{' '}
            <span className="font-mono text-base text-neutral-500">{stock.code}</span>
          </h2>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => saveToJournal()}
            className="btn-orange rounded-full px-4 py-2 text-xs font-bold"
          >
            저널 저장
          </button>
          <button
            type="button"
            onClick={reanalyze}
            className="btn-outline rounded-full px-4 py-2 text-xs font-bold"
          >
            재분석
          </button>
        </div>
      </div>

      <div className="grid gap-8 lg:grid-cols-12">
        <div className="lg:col-span-5">
          <div className="card-orange rounded-2xl p-8">
            <span className="font-mono text-xs uppercase text-white/80">종합 신뢰도</span>
            <div className="my-4 text-7xl font-black">{stock.score}</div>
            <div className="mb-4 grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-white/15 p-3">
                <span className="text-[10px] text-white/70">방향</span>
                <span className="block text-sm font-bold">{stock.direction}</span>
              </div>
              <div className="rounded-lg bg-white/15 p-3">
                <span className="text-[10px] text-white/70">일치</span>
                <span className="block text-sm font-bold">{stock.consistency}</span>
              </div>
            </div>
            <div className="h-2 rounded-full bg-black/20">
              <div
                className="h-full rounded-full bg-white"
                style={{ width: `${stock.score}%` }}
              />
            </div>
            {stock.score >= 70 && (
              <p className="mt-4 flex items-center gap-1 text-sm font-bold">
                <TrendingUp className="h-4 w-4" />+{pct}% vs 컨센서스
              </p>
            )}
            <p className="mt-4 border-t border-white/20 pt-4 text-xs leading-relaxed text-white/85">
              &quot;{stock.summary}&quot;
            </p>
          </div>
        </div>
        <div className="space-y-4 lg:col-span-7">
          <span className="font-mono text-xs uppercase text-neutral-500">에이전트 원천</span>
          {agentCard(
            'dart',
            FileText,
            'DART Watcher',
            stock.dart.score,
            `공시 ${stock.dart.disclosures?.length || 0}건`,
          )}
          {agentCard(
            'report',
            Database,
            'Report RAG',
            stock.report.score,
            stock.report.conflict ? '의견 충돌' : '안정',
          )}
          {agentCard(
            'alt',
            Sliders,
            'Alternative',
            stock.alt.score,
            `채용 ${stock.alt.hiring?.pct}%`,
          )}
        </div>
      </div>

      <div className="grid gap-4 md:grid-cols-2">
        <div className="card-white rounded-xl border-orange-100 p-5 text-neutral-800 shadow-sm">
          <span className="text-xs font-bold text-orange-600">Bull</span>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-sm">
            {stock.debate.bull.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
        <div className="card-light rounded-xl border-red-200 bg-red-50/30 p-5">
          <span className="text-xs font-bold text-red-600">Bear</span>
          <ul className="mt-2 list-disc space-y-1 pl-4 text-sm text-neutral-600">
            {stock.debate.bear.map((b) => (
              <li key={b}>{b}</li>
            ))}
          </ul>
        </div>
      </div>

      <div className="card-light rounded-xl p-5 text-sm text-neutral-700 shadow-sm">
        <strong className="text-neutral-900">Judge:</strong> {stock.debate.judge}
      </div>

      <div className="text-center">
        <Link
          href={`/dashboard/quote?code=${stock.code}`}
          className="btn-outline mt-2 rounded-full px-6 py-2.5 text-sm font-bold"
        >
          종목 시세 보기 →
        </Link>
      </div>
    </div>
  );
}
