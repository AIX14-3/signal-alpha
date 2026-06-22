import Link from 'next/link';
import { TrendingUp } from 'lucide-react';
import { dirChipClass, dirTextClass, fmtWon } from '@/lib/signal/utils';
import type { DrilldownKey, StockData } from '@/types/signal';

function DrilldownDart({ dart }: { dart: StockData['dart'] }) {
  const pct = Math.round(
    ((dart.surprise.actual - dart.surprise.consensus) / dart.surprise.consensus) * 100,
  );

  return (
    <div className="space-y-6">
      <div className="flex items-center gap-4">
        <div className="card-orange shrink-0 rounded-xl px-6 py-4 text-center">
          <span className="text-[10px] uppercase text-white/80">임팩트</span>
          <div className="text-4xl font-black">{dart.score}</div>
        </div>
        <div>
          <span className="text-xs text-neutral-500">방향</span>
          <p className={`text-lg font-bold ${dirTextClass(dart.direction)}`}>{dart.direction}</p>
        </div>
      </div>
      <div className="card-light rounded-xl p-5">
        <h4 className="mb-3 text-xs font-bold uppercase text-neutral-500">어닝 서프라이즈</h4>
        <div className="grid grid-cols-2 gap-4 text-sm">
          <div>
            <span className="text-xs text-neutral-500">실적</span>
            <p className="text-xl font-black text-neutral-900">
              {dart.surprise.actual}{' '}
              <span className="text-xs font-normal text-neutral-500">{dart.surprise.unit}</span>
            </p>
          </div>
          <div>
            <span className="text-xs text-neutral-500">컨센서스</span>
            <p className="text-lg font-bold text-neutral-600">
              {dart.surprise.consensus}{' '}
              <span className="text-xs font-normal">{dart.surprise.unit}</span>
            </p>
          </div>
        </div>
        <p className="mt-3 flex items-center gap-1 text-sm font-bold text-orange-600">
          <TrendingUp className="h-4 w-4" />
          {pct >= 0 ? '+' : ''}
          {pct}% vs 컨센서스
        </p>
      </div>
      <div>
        <h4 className="mb-3 text-xs font-bold uppercase text-neutral-500">
          최근 공시 {dart.disclosures?.length || 0}건
        </h4>
        <div className="space-y-3">
          {(dart.disclosures || []).map((d) => (
            <Link
              key={d.title}
              href={d.link}
              target="_blank"
              rel="noopener noreferrer"
              className="card-light block rounded-xl p-4 transition-colors hover:border-orange-300"
            >
              <div className="flex justify-between gap-2">
                <span className="text-[10px] text-neutral-500">
                  {d.date} · {d.type}
                </span>
                <span className={`rounded border px-2 py-0.5 text-[10px] ${dirChipClass(d.impact)}`}>
                  {d.impact}
                </span>
              </div>
              <p className="mt-2 text-sm font-semibold text-neutral-900">{d.title}</p>
            </Link>
          ))}
        </div>
      </div>
    </div>
  );
}

function DrilldownReport({ report }: { report: StockData['report'] }) {
  const trend = report.trend || [];
  const max = Math.max(...trend, 1);

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="card-orange rounded-xl px-6 py-4 text-center">
          <span className="text-[10px] uppercase text-white/80">RAG 점수</span>
          <div className="text-4xl font-black">{report.score}</div>
        </div>
        {report.conflict ? (
          <span className="rounded-full border border-amber-200 bg-amber-100 px-3 py-1.5 text-xs font-bold text-amber-800">
            의견 충돌 감지
          </span>
        ) : (
          <span className="rounded-full border border-green-200 bg-green-50 px-3 py-1.5 text-xs font-bold text-green-700">
            컨센서스 안정
          </span>
        )}
      </div>
      <div className="card-light overflow-x-auto rounded-xl p-5">
        <h4 className="mb-3 text-xs font-bold uppercase text-neutral-500">증권사 의견</h4>
        <table className="w-full text-left text-sm">
          <thead>
            <tr className="text-[10px] uppercase text-neutral-500">
              <th className="pb-2">증권사</th>
              <th className="pb-2">의견</th>
              <th className="pb-2">목표가</th>
              <th className="pb-2">일자</th>
            </tr>
          </thead>
          <tbody>
            {(report.opinions || []).map((o) => (
              <tr key={o.firm} className="border-t border-neutral-100">
                <td className="py-3 pr-2 text-sm font-semibold">{o.firm}</td>
                <td className="py-3">
                  <span
                    className={`rounded px-2 py-1 text-xs font-bold ${
                      o.rating === 'BUY'
                        ? 'bg-orange-100 text-orange-700'
                        : o.rating === 'SELL'
                          ? 'bg-red-50 text-red-600'
                          : 'bg-neutral-100 text-neutral-600'
                    }`}
                  >
                    {o.rating}
                  </span>
                </td>
                <td className="py-3 font-mono text-sm">{o.target}</td>
                <td className="py-3 text-xs text-neutral-500">{o.date}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="card-light rounded-xl p-5">
        <h4 className="mb-3 text-xs font-bold uppercase text-neutral-500">목표가 추세</h4>
        <div className="flex h-24 items-end gap-1">
          {trend.map((v) => (
            <div
              key={v}
              className="flex-1 rounded-t bg-orange-200 transition-colors hover:bg-orange-400"
              style={{ height: `${Math.round((v / max) * 100)}%` }}
              title={fmtWon(v)}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function DrilldownAlt({ alt }: { alt: StockData['alt'] }) {
  const trend = alt.datalab?.trend || [];
  const max = Math.max(...trend, 1);
  const hirePct = alt.hiring?.pct ?? 0;
  const patentGrowth = alt.patent?.growth ?? 0;

  return (
    <div className="space-y-6">
      <div className="card-orange inline-block rounded-xl px-6 py-4">
        <span className="text-[10px] uppercase text-white/80">Alt 점수</span>
        <div className="text-4xl font-black">{alt.score}</div>
      </div>
      <div className="grid grid-cols-2 gap-4">
        <div className="card-light rounded-xl p-5">
          <span className="text-xs text-neutral-500">채용 YoY</span>
          <p
            className={`mt-1 text-3xl font-black ${hirePct >= 0 ? 'text-orange-600' : 'text-red-600'}`}
          >
            {hirePct >= 0 ? '+' : ''}
            {hirePct}%
          </p>
        </div>
        <div className="card-light rounded-xl p-5">
          <span className="text-xs text-neutral-500">특허 증가</span>
          <p
            className={`mt-1 text-3xl font-black ${patentGrowth >= 0 ? 'text-orange-600' : 'text-red-600'}`}
          >
            {patentGrowth >= 0 ? '+' : ''}
            {patentGrowth}%
          </p>
        </div>
      </div>
      <div className="card-light rounded-xl p-5">
        <h4 className="mb-3 text-xs font-bold uppercase text-neutral-500">DataLab 지수</h4>
        <div className="flex h-24 items-end gap-1">
          {trend.map((v) => (
            <div
              key={v}
              className="flex-1 rounded-t bg-orange-200"
              style={{ height: `${Math.round((v / max) * 100)}%` }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

export function DrilldownContent({ drillKey, stock }: { drillKey: DrilldownKey; stock: StockData }) {
  if (drillKey === 'dart') return <DrilldownDart dart={stock.dart} />;
  if (drillKey === 'report') return <DrilldownReport report={stock.report} />;
  if (drillKey === 'alt') return <DrilldownAlt alt={stock.alt} />;
  return <p className="text-sm text-neutral-500">상세 데이터가 없습니다.</p>;
}
