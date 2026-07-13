"use client";

// 라운드트립 1건 부검 카드 — Plan vs Actual + 3분류 판정 + 그때 관측 가능했던 신호(PIT).
// 사후 고점/저점으로 비난하지 않는다 — 관측 신호 기반 서술만.

import type { RoundTrip } from "@/lib/apiClient";
import {
  formatDate,
  formatPct,
  formatWon,
  pnlClass,
  signalKindLabel,
  verdictLabel,
} from "./util";

const TONE_CLASS: Record<string, string> = {
  ok: "pill up",
  warn: "pill down",
  neutral: "pill flat",
};

export function RoundTripCard({ trip }: { trip: RoundTrip }) {
  const verdict = verdictLabel(trip.classification);
  const pva = trip.plan_vs_actual;

  return (
    <li className="card px-5 py-4" data-flow="postmortem-roundtrip">
      <div className="flex items-center justify-between">
        <span className="text-[13px] text-muted">
          {formatDate(trip.opened_at)} → {trip.is_open ? "보유 중" : formatDate(trip.closed_at)}
          {trip.holding_days !== null && !trip.is_open ? ` · ${trip.holding_days}일 보유` : ""}
        </span>
        {/* 미청산(보유 중)은 우측 손익 배지를 표시하지 않는다 — 좌측 "보유 중"으로 이미 드러난다. */}
        {!trip.is_open && (
          <span className={`text-[15px] font-bold ${pnlClass(trip.realized_pnl_pct)}`}>
            {formatPct(trip.realized_pnl_pct)}
          </span>
        )}
      </div>

      <div className="mt-1 text-[13px] text-navy-soft">
        평균 매수 {formatWon(trip.avg_buy_price)}
        {trip.avg_sell_price ? ` · 평균 매도 ${formatWon(trip.avg_sell_price)}` : ""}
      </div>

      {/* 3분류 판정 */}
      <div className="mt-3">
        <span className={TONE_CLASS[verdict.tone]}>{verdict.title}</span>
      </div>

      {/* 계획 대비 실제 */}
      {pva.has_plan && pva.evaluated ? (
        <div className="mt-3 rounded-lg border border-line bg-white px-4 py-3 text-[13px] text-navy-soft">
          <p className="font-semibold text-navy">계획 대비 실제</p>
          {pva.thesis ? <p className="mt-1">근거: {pva.thesis}</p> : null}
          {pva.planned_stop_pct !== undefined ? (
            <p className="mt-1">
              손절 계획 {formatPct(pva.planned_stop_pct)}
              {pva.stop_violated
                ? " → 실제 청산이 손절선보다 더 나빴습니다(규칙 이탈)."
                : " → 손절 규칙을 지켰습니다."}
            </p>
          ) : null}
          {pva.planned_target_pct !== undefined ? (
            <p className="mt-1">
              목표 {formatPct(pva.planned_target_pct)}
              {pva.reached_target ? " 도달" : " 미도달"}
            </p>
          ) : null}
        </div>
      ) : pva.has_plan ? null : (
        <p className="mt-3 text-[13px] text-muted">
          매수 계획(목표가·손절가)이 기록되지 않아 규칙 대비 부검을 할 수 없습니다.
        </p>
      )}

      {/* 그때 관측 가능했던 신호 */}
      {trip.observed_signals.length > 0 ? (
        <div className="mt-3">
          <p className="text-[12.5px] font-semibold text-navy">
            보유 구간에 관측 가능했던 신호 {trip.observed_signals.length}건
          </p>
          <ul className="mt-1 space-y-1">
            {trip.observed_signals.map((s, i) => (
              <li key={i} className="text-[12.5px] text-navy-soft">
                {formatDate(s.signal_date)} {signalKindLabel(s.kind)}
                {detailHolder(s.detail) ? ` (${detailHolder(s.detail)})` : ""}
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </li>
  );
}

function detailHolder(detail: Record<string, unknown> | null): string {
  if (!detail) return "";
  const holder = detail["holder_name"];
  return typeof holder === "string" ? holder : "";
}
