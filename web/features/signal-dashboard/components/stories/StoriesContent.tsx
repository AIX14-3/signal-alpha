'use client';

import Link from 'next/link';
import { useEffect } from 'react';
import { useSearchParams } from 'next/navigation';
import { STORY_ITEMS } from '@/lib/signal/mock-data';
import { dirChipClass } from '@/lib/signal/utils';
import type { StoryFilter } from '@/types/signal';
import { useDashboard } from '../../context/DashboardContext';
import { PageShell } from '../layout/PageShell';
import { Timeline } from '../shared/Timeline';

const FILTERS: { id: StoryFilter; l: string }[] = [
  { id: 'all', l: '전체' },
  { id: 'positive', l: '강세' },
  { id: 'mixed', l: '혼조' },
  { id: 'negative', l: '약세' },
];

export function StoriesContent() {
  const searchParams = useSearchParams();
  const { storyFilter, setStoryFilter, selectStock } = useDashboard();

  useEffect(() => {
    const filter = searchParams.get('filter') as StoryFilter | null;
    if (filter && FILTERS.some((f) => f.id === filter)) {
      setStoryFilter(filter);
    }
  }, [searchParams, setStoryFilter]);

  const items = STORY_ITEMS.filter((s) => {
    if (storyFilter === 'all') return true;
    if (storyFilter === 'positive') return s.direction === 'POSITIVE';
    if (storyFilter === 'mixed') return s.direction === 'MIXED';
    return s.direction === 'NEGATIVE';
  });

  const featured = items.find((s) => s.code === '000660') || items[0];
  const others = items.filter((s) => s.code !== featured?.code);

  return (
    <PageShell
      title="데이터가 말해주는 성장 스토리"
      subtitle="분석 이력·종목별 성과 서사"
      cta={
        <div className="mt-10 text-center">
          <Link href="/?focus=search" className="btn-orange rounded-full px-8 py-3 text-sm font-bold">
            종목 추가 분석 →
          </Link>
        </div>
      }
    >
      <div className="mb-8 flex flex-wrap gap-2">
        {FILTERS.map((f) => (
          <Link
            key={f.id}
            href={`/stories?filter=${f.id}`}
            onClick={() => setStoryFilter(f.id)}
            className={`rounded-full border px-4 py-2 text-xs font-bold ${
              storyFilter === f.id
                ? 'card-orange border-transparent text-white'
                : 'btn-outline'
            }`}
          >
            {f.l}
          </Link>
        ))}
      </div>

      {featured && (
        <button
          type="button"
          onClick={() => selectStock(featured.code)}
          className="card-orange mb-8 w-full cursor-pointer rounded-2xl p-8 text-left shadow-lg"
        >
          <div className="flex flex-wrap justify-between gap-4">
            <div>
              <span className="text-xs font-bold text-white/80">Featured</span>
              <h3 className="mt-1 text-2xl font-black">{featured.name}</h3>
              <p className="mt-2 text-sm text-white/90">{featured.summary}</p>
            </div>
            <div className="text-right">
              <span className="text-5xl font-black">{featured.score}</span>
              <p className="mt-1 text-sm font-bold">
                +{featured.growth}% · {featured.consistency}
              </p>
            </div>
          </div>
          <Timeline item={featured} />
        </button>
      )}

      <div className="mb-10 grid gap-4 md:grid-cols-2">
        {others.map((item) => (
          <button
            key={item.code}
            type="button"
            onClick={() => selectStock(item.code)}
            className="card-light cursor-pointer rounded-xl p-5 text-left shadow-sm hover:border-orange-400"
          >
            <h4 className="font-bold">{item.name}</h4>
            <p className="mt-2 text-2xl font-black text-orange-600">{item.score}점</p>
            <span
              className={`mt-2 inline-block rounded border px-2 py-0.5 text-[10px] ${dirChipClass(item.direction)}`}
            >
              {item.direction}
            </span>
            <p className="mt-2 text-xs text-neutral-500">
              {item.growth >= 0 ? '+' : ''}
              {item.growth}%
            </p>
          </button>
        ))}
      </div>

      <section className="card-light mb-8 overflow-x-auto rounded-2xl shadow-sm">
        <table className="w-full min-w-[520px] text-left text-sm">
          <thead>
            <tr className="bg-neutral-50 text-[10px] uppercase text-neutral-500">
              <th className="px-5 py-3 text-left">종목</th>
              <th className="px-4 py-3 text-left">점수</th>
              <th className="px-4 py-3 text-left">DART</th>
              <th className="px-4 py-3 text-left">Report</th>
              <th className="px-4 py-3 text-left">Alt</th>
              <th className="px-5 py-3 text-left">일치성</th>
            </tr>
          </thead>
          <tbody>
            {items.map((s) => (
              <tr
                key={s.code}
                onClick={() => selectStock(s.code)}
                className="cursor-pointer border-t border-neutral-100 hover:bg-orange-50"
              >
                <td className="px-5 py-3 font-semibold">{s.name}</td>
                <td className="px-4 py-3 font-black">{s.score}</td>
                <td className="px-4 py-3">{s.dart}</td>
                <td className="px-4 py-3">{s.report}</td>
                <td className="px-4 py-3">{s.alt}</td>
                <td className="px-5 py-3">
                  <span
                    className={`rounded border px-2 py-0.5 text-[10px] ${dirChipClass(s.direction)}`}
                  >
                    {s.consistency}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <div className="card-light rounded-xl border-l-4 border-orange-500 p-5 shadow-sm">
        <p className="text-sm text-neutral-700">
          <strong>인사이트:</strong> 채용·특허가 공시보다 2~4주 선행합니다. SK하이닉스는 4단계
          모두 상승, 네이버는 채용↓ → 검색↓ → 공시 부정 흐름입니다.
        </p>
      </div>
    </PageShell>
  );
}
