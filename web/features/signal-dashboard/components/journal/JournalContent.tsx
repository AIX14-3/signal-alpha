'use client';

import Link from 'next/link';
import { ArrowLeft } from 'lucide-react';
import { MOCK_ALERTS } from '@/lib/signal/mock-data';
import { getPatternRatio } from '@/lib/signal/utils';
import { useDashboard } from '../../context/DashboardContext';

export function JournalContent() {
  const { journal, toggleTrade, updateJournalNote } = useDashboard();
  const ratio = getPatternRatio(journal);
  const notTrade = 100 - ratio;

  return (
    <div className="mx-auto max-w-5xl space-y-10 px-4 py-12">
      <div className="flex items-center justify-between border-b border-neutral-200 pb-6">
        <div className="flex items-center gap-3">
          <Link
            href="/"
            className="rounded-full border border-neutral-300 p-2 text-neutral-700"
          >
            <ArrowLeft className="h-4 w-4" />
          </Link>
          <h2 className="text-2xl font-black text-neutral-900">Signal Journal</h2>
        </div>
        <Link href="/?focus=search" className="btn-orange rounded-full px-5 py-2 text-xs font-bold">
          + 새 분석
        </Link>
      </div>

      <div className="grid gap-4 md:grid-cols-3">
        <div className="card-white rounded-xl p-6 text-neutral-900 shadow-sm">
          <span className="text-xs text-neutral-500">누적</span>
          <div className="mt-1 text-4xl font-black">{journal.length}</div>
        </div>
        <div className="card-orange rounded-xl p-6">
          <span className="text-xs text-white/80">투자 연계</span>
          <div className="mt-1 text-4xl font-black">{ratio}%</div>
        </div>
        <div className="card-light rounded-xl p-6 shadow-sm">
          <span className="text-xs text-neutral-500">급변 알림</span>
          <div className="mt-1 font-bold text-orange-600">±15점 ACTIVE</div>
        </div>
      </div>

      <div className="grid gap-6 md:grid-cols-2">
        <div className="card-light rounded-xl p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-bold">±15점 급변 알림</h3>
          <div className="space-y-2">
            {MOCK_ALERTS.map((a) => (
              <div
                key={a.id}
                className={`flex gap-3 rounded-lg p-3 ${
                  a.read ? 'bg-neutral-50' : 'border border-orange-100 bg-orange-50'
                }`}
              >
                <div className="flex-1">
                  <p className="text-sm font-semibold text-neutral-900">{a.name}</p>
                  <p className="text-xs text-neutral-600">{a.msg}</p>
                  <span className="text-[10px] text-neutral-400">{a.time}</span>
                </div>
                {!a.read && (
                  <span className="mt-2 h-2 w-2 shrink-0 rounded-full bg-orange-500" />
                )}
              </div>
            ))}
          </div>
        </div>
        <div className="card-light rounded-xl p-5 shadow-sm">
          <h3 className="mb-3 text-sm font-bold">패턴 인사이트</h3>
          <p className="mb-4 text-xs text-neutral-600">
            긍정 시그널(70점+) 중 실제 매매 비율{' '}
            <strong className="text-orange-600">{ratio}%</strong>
          </p>
          <div className="flex h-8 overflow-hidden rounded-full text-[10px] font-bold">
            {ratio > 0 && (
              <div
                className="flex items-center justify-center bg-orange-500 text-white"
                style={{ width: `${ratio}%` }}
              >
                {ratio >= 10 ? `매매 ${ratio}%` : null}
              </div>
            )}
            {notTrade > 0 && (
              <div
                className="flex items-center justify-center bg-neutral-200 text-neutral-600"
                style={{ width: `${notTrade}%` }}
              >
                {notTrade >= 10 ? `관망 ${notTrade}%` : null}
              </div>
            )}
          </div>
          <p className="mt-3 text-[10px] text-neutral-500">
            &quot;이 시그널을 봤을 때 나는 어떤 판단을 했는가&quot; — 자기 검열
          </p>
        </div>
      </div>

      <div>
        <h3 className="mb-4 text-sm font-bold text-neutral-900">판단 기록</h3>
        {journal.map((item) => (
          <div
            key={item.id}
            className="card-light mb-4 flex flex-col gap-6 rounded-xl p-6 shadow-sm md:flex-row"
          >
            <div className="flex-1">
              <h4 className="font-bold text-neutral-900">{item.name}</h4>
              <span className="text-xs text-neutral-500">
                {item.code} · {item.date}
              </span>
              <textarea
                value={item.note}
                onChange={(e) => updateJournalNote(item.id, e.target.value)}
                className="mt-3 h-16 w-full resize-none rounded-lg border border-neutral-200 bg-neutral-50 p-3 text-xs text-neutral-700"
              />
            </div>
            <div className="flex flex-col items-end gap-3 md:w-44">
              <span className="text-2xl font-black text-orange-600">{item.score}</span>
              <button
                type="button"
                onClick={() => toggleTrade(item.id)}
                className={`w-full rounded-full py-2 text-xs font-bold border ${
                  item.trade
                    ? 'card-orange border-transparent text-white'
                    : 'border-neutral-300 text-neutral-600'
                }`}
              >
                {item.trade ? '매매 진입' : '관망'}
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
