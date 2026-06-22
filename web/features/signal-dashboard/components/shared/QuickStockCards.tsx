'use client';

import Link from 'next/link';
import { STOCK_DATA_TEMPLATES, STORY_ITEMS } from '@/lib/signal/mock-data';
import { dirChipClass, fmtWon } from '@/lib/signal/utils';
import { useDashboard } from '../../context/DashboardContext';

export function QuickStockCards() {
  const { selectStock } = useDashboard();

  return (
    <>
      {STORY_ITEMS.map((item) => {
        const q = STOCK_DATA_TEMPLATES[item.code].quote;
        const up = q.change >= 0;
        return (
          <div key={item.code} className="card-light rounded-2xl p-6 shadow-sm transition-colors">
            <button
              type="button"
              onClick={() => selectStock(item.code)}
              className="-m-2 cursor-pointer rounded-xl p-2 hover:border-orange-400"
            >
              <div className="flex items-start justify-between">
                <span className="font-mono text-xs text-neutral-500">{item.code}</span>
                <span className={`rounded border px-2 py-0.5 text-[10px] ${dirChipClass(item.direction)}`}>
                  {item.direction}
                </span>
              </div>
              <h3 className="mt-2 text-lg font-black text-neutral-900">{item.name}</h3>
              <p className="mt-1 text-xl font-black text-neutral-900">
                {fmtWon(q.price)}
                <span className="text-sm font-normal text-neutral-500">원</span>
              </p>
              <p className={`mt-0.5 text-xs font-bold ${up ? 'text-green-600' : 'text-red-600'}`}>
                {up ? '+' : ''}
                {fmtWon(q.change)} ({up ? '+' : ''}
                {q.changePct}%)
              </p>
              <p className="mt-3 text-2xl font-black text-orange-600">
                {item.score}
                <span className="text-sm font-normal text-neutral-400"> Signal</span>
              </p>
            </button>
            <Link
              href={`/quote?code=${item.code}`}
              className="mt-3 block w-full text-center text-xs font-bold text-orange-600 hover:underline"
            >
              시세 상세 →
            </Link>
          </div>
        );
      })}
    </>
  );
}
