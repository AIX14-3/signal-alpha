"use client";

// 매매 의사결정 부검 — 수기 체결 입력·계획·단건/패턴 부검. 구독 전용.
// 스탠스: 예측 아님·사후확신 없음. "그때 관측 가능했던 신호"로만 판단한다.

import Link from "next/link";
import { useEffect, useState } from "react";

import { RoundTripCard } from "@/components/postmortem/RoundTripCard";
import { formatDate, formatPct } from "@/components/postmortem/util";
import type { PostmortemNarrative } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { usePostmortemStore } from "@/stores/postmortemStore";

export default function PostmortemPage() {
  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const subscribed = user?.subscription_active === true;

  const loadOverview = usePostmortemStore((s) => s.loadOverview);
  const error = usePostmortemStore((s) => s.error);

  useEffect(() => {
    if (subscribed) void loadOverview();
  }, [subscribed, loadOverview]);

  if (status !== "authenticated" || !user) {
    return (
      <main data-page="postmortem" className="py-16 text-center">
        <p className="font-bold">로그인이 필요합니다.</p>
        <Link href="/login" className="brand-grad mt-4 inline-block rounded-full px-6 py-2.5 text-[14px] font-bold text-white">
          로그인
        </Link>
      </main>
    );
  }

  if (!subscribed) {
    return (
      <main data-page="postmortem">
        <div className="card px-6 py-8 text-center" data-flow="postmortem-subscribe">
          <p className="font-bold">매매 부검은 구독 회원 전용 기능입니다.</p>
          <p className="mt-2 text-[13.5px] text-muted">
            내 실매매를 사후확신 없이 부검합니다 — 계획 대비 실제, 그때 관측 가능했던 신호로.
          </p>
          <Link href="/pricing" className="brand-grad mt-4 inline-block rounded-full px-6 py-2.5 text-[14px] font-bold text-white">
            구독 안내 보기
          </Link>
        </div>
      </main>
    );
  }

  return (
    <main data-page="postmortem" className="space-y-8">
      <header>
        <h1 className="text-[22px] font-bold text-navy">매매 의사결정 부검</h1>
        <p className="mt-1 text-[13.5px] text-muted">
          예측이 아니라 사후 사실 분석입니다. 고점/저점 훈수 대신 &quot;그때 볼 수 있었던 신호&quot;로만 판단합니다.
        </p>
      </header>

      {error ? <p className="text-[13.5px] text-red">{error}</p> : null}

      <ManualFillSection />
      <PlanSection />
      <TradeLookupSection />
      <PatternSection />
    </main>
  );
}

// ---- 매수 계획(선택) ----------------------------------------------------
function PlanSection() {
  const plans = usePostmortemStore((s) => s.plans);
  const savePlan = usePostmortemStore((s) => s.savePlan);
  const removePlan = usePostmortemStore((s) => s.removePlan);
  const [code, setCode] = useState("");
  const [thesis, setThesis] = useState("");
  const [target, setTarget] = useState("");
  const [stop, setStop] = useState("");
  const [sell, setSell] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const num = (v: string): number | null => {
    const n = Number(v);
    return v.trim() && !Number.isNaN(n) ? n : null;
  };

  return (
    <section data-panel="postmortem-plans">
      <h2 className="text-[16px] font-bold text-navy">매수 계획 기록 (선택)</h2>
      <p className="mt-1 text-[13px] text-muted">
        매수 시 목표가·손절가를 적어두면, 나중에 실제 매매를 내 규칙과 대조해 부검합니다.
      </p>

      {plans.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {plans.map((p) => (
            <li key={p.id} className="card flex items-center justify-between px-4 py-3 text-[13px]">
              <span className="text-navy-soft">
                <span className="font-semibold text-navy">{p.stock_code}</span>
                {p.target_price ? ` · 목표 ${p.target_price}` : ""}
                {p.stop_price ? ` · 손절 ${p.stop_price}` : ""}
              </span>
              <button type="button" onClick={() => void removePlan(p.stock_code)} className="text-[12.5px] text-muted hover:text-red">
                삭제
              </button>
            </li>
          ))}
        </ul>
      ) : null}

      <form
        className="card mt-3 space-y-2 px-5 py-4"
        data-flow="postmortem-plan-save"
        onSubmit={async (e) => {
          e.preventDefault();
          if (!code.trim()) return;
          setBusy(true);
          setErr(null);
          try {
            await savePlan({
              stock_code: code.trim(),
              thesis,
              target_price: num(target),
              stop_price: num(stop),
              sell_condition: sell.trim() || null,
            });
            setCode("");
            setThesis("");
            setTarget("");
            setStop("");
            setSell("");
          } catch (error) {
            // 실패 시 입력을 보존하고 사유를 표시한다(unhandled rejection 방지).
            setErr(error instanceof Error ? error.message : "계획 저장에 실패했습니다.");
          } finally {
            setBusy(false);
          }
        }}
      >
        <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="종목코드 (예: 005930)" value={code} onChange={(e) => setCode(e.target.value)} />
        <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="매수 근거(thesis)" value={thesis} onChange={(e) => setThesis(e.target.value)} />
        <div className="flex gap-2">
          <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="목표가" inputMode="numeric" value={target} onChange={(e) => setTarget(e.target.value)} />
          <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="손절가" inputMode="numeric" value={stop} onChange={(e) => setStop(e.target.value)} />
        </div>
        <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="매도 조건 (예: 실적 발표 후)" value={sell} onChange={(e) => setSell(e.target.value)} />
        {err ? <p className="text-[13px] text-red">{err}</p> : null}
        <button type="submit" disabled={busy || !code.trim()} className="brand-grad rounded-full px-5 py-2 text-[13px] font-bold text-white disabled:opacity-60">
          {busy ? "저장 중…" : "계획 저장"}
        </button>
      </form>
    </section>
  );
}

// ---- 수기 매매 기록 -----------------------------------------------------
function ManualFillSection() {
  const fills = usePostmortemStore((s) => s.fills);
  const addFill = usePostmortemStore((s) => s.addFill);
  const removeFill = usePostmortemStore((s) => s.removeFill);

  const [code, setCode] = useState("");
  const [side, setSide] = useState<"buy" | "sell">("buy");
  const [date, setDate] = useState("");
  const [qty, setQty] = useState("");
  const [price, setPrice] = useState("");
  const [fee, setFee] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const numOrNull = (v: string): number | null => {
    // 천 단위 콤마(예: "70,000")를 허용 — Number("70,000")=NaN 이라 그대로면 조용히 거부된다.
    const cleaned = v.replace(/,/g, "").trim();
    if (!cleaned) return null;
    const n = Number(cleaned);
    return Number.isNaN(n) ? null : n;
  };

  const canSubmit = code.trim() && date && numOrNull(qty) !== null && numOrNull(price) !== null;

  return (
    <section data-panel="postmortem-fills">
      <h2 className="text-[16px] font-bold text-navy">매매 기록 입력</h2>
      <p className="mt-1 text-[13px] text-muted">
        매수·매도 체결을 직접 입력하면, 이를 라운드트립으로 묶어 부검합니다. 수량·가격은 체결 단위로 적어주세요.
      </p>

      {fills.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {fills.map((f) => (
            <li key={f.id} className="card flex items-center justify-between px-4 py-3 text-[13px]">
              <span className="text-navy-soft">
                <span className={`font-semibold ${f.side === "buy" ? "text-red" : "text-sky-deep"}`}>
                  {f.side === "buy" ? "매수" : "매도"}
                </span>
                <span className="ml-2 font-semibold text-navy">{f.stock_code}</span>
                <span className="ml-2 text-muted">{f.filled_at ? formatDate(f.filled_at) : ""}</span>
                <span className="ml-2">{f.quantity ?? "-"}주 · {f.price ?? "-"}</span>
                {f.fee ? <span className="ml-2 text-muted">수수료 {f.fee}</span> : null}
              </span>
              <button type="button" onClick={() => void removeFill(f.id)} className="text-[12.5px] text-muted hover:text-red">
                삭제
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-[13.5px] text-muted">입력된 매매 기록이 없습니다. 아래에서 첫 체결을 기록하세요.</p>
      )}

      <form
        className="card mt-3 space-y-2 px-5 py-4"
        data-flow="postmortem-fill-add"
        onSubmit={async (e) => {
          e.preventDefault();
          const quantity = numOrNull(qty);
          const priceNum = numOrNull(price);
          if (!code.trim() || !date || quantity === null || priceNum === null) return;
          setBusy(true);
          setErr(null);
          try {
            await addFill({
              stock_code: code.trim(),
              side,
              filled_at: date,
              quantity,
              price: priceNum,
              fee: numOrNull(fee),
            });
            setCode("");
            setDate("");
            setQty("");
            setPrice("");
            setFee("");
          } catch (error) {
            // 실패 시 입력을 보존하고 사유를 표시한다(unhandled rejection 방지).
            setErr(error instanceof Error ? error.message : "매매 기록 저장에 실패했습니다.");
          } finally {
            setBusy(false);
          }
        }}
      >
        <div className="flex gap-2">
          {(["buy", "sell"] as const).map((s) => (
            <button
              type="button"
              key={s}
              onClick={() => setSide(s)}
              className={`pill flat text-[13px] ${side === s ? "!border-sky !text-sky-deep font-bold" : ""}`}
            >
              {s === "buy" ? "매수" : "매도"}
            </button>
          ))}
        </div>
        <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="종목코드 (예: 005930)" value={code} onChange={(e) => setCode(e.target.value)} />
        <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" type="date" aria-label="체결일" value={date} onChange={(e) => setDate(e.target.value)} />
        <div className="flex gap-2">
          <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="수량(주)" inputMode="decimal" value={qty} onChange={(e) => setQty(e.target.value)} />
          <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="체결가" inputMode="decimal" value={price} onChange={(e) => setPrice(e.target.value)} />
        </div>
        <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="수수료(선택)" inputMode="decimal" value={fee} onChange={(e) => setFee(e.target.value)} />
        {err ? <p className="text-[13px] text-red">{err}</p> : null}
        <button type="submit" disabled={busy || !canSubmit} className="brand-grad rounded-full px-5 py-2 text-[13px] font-bold text-white disabled:opacity-60">
          {busy ? "저장 중…" : "매매 기록 추가"}
        </button>
      </form>
    </section>
  );
}

// ---- 단건 부검(종목 조회) ----------------------------------------------
function TradeLookupSection() {
  const trade = usePostmortemStore((s) => s.trade);
  const loading = usePostmortemStore((s) => s.loading);
  const loadTrade = usePostmortemStore((s) => s.loadTrade);
  const clearTrade = usePostmortemStore((s) => s.clearTrade);
  const [code, setCode] = useState("");

  return (
    <section data-panel="postmortem-trade">
      <h2 className="text-[16px] font-bold text-navy">종목별 부검</h2>
      <form
        className="mt-3 flex gap-2"
        onSubmit={(e) => {
          e.preventDefault();
          const clean = code.trim();
          if (clean) void loadTrade(clean);
        }}
      >
        <input
          className="card flex-1 px-4 py-2.5 text-[13.5px] outline-none focus:border-sky"
          placeholder="종목코드 (예: 005930)"
          value={code}
          onChange={(e) => setCode(e.target.value)}
        />
        <button type="submit" className="rounded-full border border-line px-5 py-2.5 text-[13px] font-semibold text-navy-soft hover:border-navy hover:text-navy">
          부검
        </button>
      </form>

      {loading ? <p className="py-8 text-center text-muted">불러오는 중…</p> : null}

      {trade ? (
        <div className="mt-4">
          <div className="flex items-center justify-between">
            <h3 className="font-bold text-navy">
              {trade.stock_name ?? trade.stock_code} <span className="text-muted">({trade.stock_code})</span>
            </h3>
            <button type="button" onClick={clearTrade} className="text-[12.5px] text-muted hover:text-navy">
              닫기
            </button>
          </div>
          {trade.round_trips.length > 0 ? (
            <ul className="mt-3 space-y-3">
              {trade.round_trips.map((t, i) => (
                <RoundTripCard key={i} trip={t} />
              ))}
            </ul>
          ) : (
            <p className="mt-3 text-[13.5px] text-muted">이 종목의 매매 기록이 없습니다. 먼저 매매 기록을 입력하세요.</p>
          )}
          <NarrativeCard narrative={trade.narrative} />
        </div>
      ) : null}
    </section>
  );
}

// ---- 패턴 부검 ----------------------------------------------------------
function PatternSection() {
  const patterns = usePostmortemStore((s) => s.patterns);
  if (!patterns) return null;

  if (patterns.suppressed) {
    return (
      <section data-panel="postmortem-patterns">
        <h2 className="text-[16px] font-bold text-navy">매매 습관 패턴</h2>
        <p className="mt-2 text-[13.5px] text-muted">
          청산된 거래 {patterns.sample}건. 패턴 분석에는 {patterns.min_sample ?? 5}건 이상이 필요합니다.
        </p>
      </section>
    );
  }

  return (
    <section data-panel="postmortem-patterns">
      <h2 className="text-[16px] font-bold text-navy">매매 습관 패턴</h2>
      <div className="card mt-3 grid grid-cols-2 gap-4 px-5 py-4 text-[13.5px] sm:grid-cols-4">
        <Stat label="청산 거래" value={`${patterns.sample}건`} />
        <Stat label="승률" value={patterns.win_rate !== undefined ? `${Math.round(patterns.win_rate * 100)}%` : "—"} />
        <Stat label="평균 수익" value={formatPct(patterns.avg_win_pct ?? null)} />
        <Stat label="평균 손실" value={formatPct(patterns.avg_loss_pct ?? null)} />
      </div>
      {patterns.disposition_effect ? (
        <p className="mt-3 rounded-lg bg-surface-2 px-4 py-3 text-[13px] text-navy-soft">
          <span className="font-semibold text-navy">처분효과</span> — 손실 종목을 이익 종목보다 오래 보유하는 경향이 있습니다
          (평균 보유 손실 {patterns.avg_hold_loss_days ?? "—"}일 vs 이익 {patterns.avg_hold_win_days ?? "—"}일).
        </p>
      ) : null}
      <NarrativeCard narrative={patterns.narrative ?? null} />
    </section>
  );
}

// FR-7: 워커 LLM 복기 서술(사후확신 없는 중립 요약). 없으면 렌더 안 함.
function NarrativeCard({ narrative }: { narrative: PostmortemNarrative | null }) {
  if (!narrative) return null;
  return (
    <div className="card mt-3 px-5 py-4" data-flow="postmortem-narrative">
      <p className="text-[12px] font-bold text-muted">복기 요약</p>
      <p className="mt-1 whitespace-pre-wrap text-[13.5px] text-navy-soft">{narrative.summary}</p>
      {narrative.key_facts.length > 0 ? (
        <ul className="mt-2 list-disc space-y-0.5 pl-5 text-[12.5px] text-navy-soft">
          {narrative.key_facts.map((fact, i) => (
            <li key={i}>{fact}</li>
          ))}
        </ul>
      ) : null}
      <p className="mt-2 text-[11px] text-muted">기록·학습을 위한 복기이며 성과 평가·투자 권유가 아닙니다.</p>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <p className="text-[12px] text-muted">{label}</p>
      <p className="mt-0.5 font-bold text-navy">{value}</p>
    </div>
  );
}
