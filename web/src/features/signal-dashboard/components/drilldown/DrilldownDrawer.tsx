'use client';

import { X } from 'lucide-react';
import { DRILLDOWN_LABELS } from '@/lib/signal/mock-data';
import { useDashboard } from '../../context/DashboardContext';
import { DrilldownContent } from './DrilldownContent';

export function DrilldownDrawer() {
  const { activeDrilldown, selectedStock, setActiveDrilldown } = useDashboard();

  if (!activeDrilldown || !selectedStock) return null;

  const label = DRILLDOWN_LABELS[activeDrilldown] || '상세';

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <button
        type="button"
        aria-label="닫기"
        onClick={() => setActiveDrilldown(null)}
        className="absolute inset-0 bg-neutral-900/30 backdrop-blur-sm"
      />
      <div className="relative h-full w-full max-w-lg overflow-y-auto border-l border-neutral-200 bg-white p-6 shadow-2xl">
        <div className="mb-2 flex items-start justify-between">
          <div>
            <p className="text-xs font-bold uppercase text-orange-600">{label}</p>
            <h3 className="text-xl font-black text-neutral-900">{selectedStock.name}</h3>
          </div>
          <button
            type="button"
            onClick={() => setActiveDrilldown(null)}
            className="rounded-full border border-neutral-200 p-2 text-neutral-600 hover:border-orange-400"
          >
            <X className="h-5 w-5" />
          </button>
        </div>
        <div className="mt-6">
          <DrilldownContent drillKey={activeDrilldown} stock={selectedStock} />
        </div>
      </div>
    </div>
  );
}
