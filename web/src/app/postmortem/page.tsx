"use client";

// 매매 의사결정 부검 — 브로커 연동·동기화·계획·단건/패턴 부검. 구독 전용.
// 스탠스: 예측 아님·사후확신 없음. "그때 관측 가능했던 신호"로만 판단한다.

import Link from "next/link";
import { useEffect, useState } from "react";

import { RoundTripCard } from "@/components/postmortem/RoundTripCard";
import { formatDate, formatPct } from "@/components/postmortem/util";
import type { BrokerName } from "@/lib/apiClient";
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

      <BrokerSection />
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
        <button type="submit" disabled={busy || !code.trim()} className="brand-grad rounded-full px-5 py-2 text-[13px] font-bold text-white disabled:opacity-60">
          {busy ? "저장 중…" : "계획 저장"}
        </button>
      </form>
    </section>
  );
}

// ---- 브로커 연동 --------------------------------------------------------
function BrokerSection() {
  const brokers = usePostmortemStore((s) => s.brokers);
  const syncMessage = usePostmortemStore((s) => s.syncMessage);
  const connect = usePostmortemStore((s) => s.connect);
  const disconnect = usePostmortemStore((s) => s.disconnect);
  const sync = usePostmortemStore((s) => s.sync);
  const [open, setOpen] = useState(false);

  return (
    <section data-panel="postmortem-brokers">
      <div className="flex items-center justify-between">
        <h2 className="text-[16px] font-bold text-navy">증권사 연동</h2>
        <button
          type="button"
          onClick={() => void sync()}
          disabled={brokers.length === 0}
          className="rounded-full border border-line px-5 py-2 text-[13px] font-semibold text-navy-soft hover:border-navy hover:text-navy disabled:opacity-60"
        >
          체결 동기화
        </button>
      </div>

      {syncMessage ? <p className="mt-2 text-[13px] text-sky-deep">{syncMessage}</p> : null}

      {brokers.length > 0 ? (
        <ul className="mt-3 space-y-2">
          {brokers.map((b) => (
            <li key={b.id} className="card flex items-center justify-between px-4 py-3">
              <div className="text-[13.5px]">
                <span className="font-semibold text-navy">{b.broker === "kiwoom" ? "키움증권" : "토스증권"}</span>
                {b.is_mock ? <span className="pill flat ml-2 text-[11px]">모의</span> : null}
                <span className="ml-2 text-muted">{b.account_ref || "기본 계좌"}</span>
                <span className={`ml-2 text-[12px] ${b.status === "error" ? "text-red" : "text-muted"}`}>
                  {b.status === "error" ? `오류: ${b.last_error ?? ""}` : b.last_synced_at ? `최근 동기화 ${formatDate(b.last_synced_at)}` : "동기화 전"}
                </span>
              </div>
              <button
                type="button"
                onClick={() => void disconnect(b.id)}
                className="text-[12.5px] text-muted hover:text-red"
              >
                연동 해제
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-3 text-[13.5px] text-muted">연동된 증권사가 없습니다. 아래에서 API 키를 등록하세요.</p>
      )}

      {open ? (
        <BrokerConnectForm
          onDone={() => setOpen(false)}
          onSubmit={async (body) => {
            await connect(body);
            setOpen(false);
          }}
        />
      ) : (
        <button
          type="button"
          onClick={() => setOpen(true)}
          className="mt-3 text-[13px] font-semibold text-sky-deep hover:underline"
        >
          + 증권사 API 키 등록
        </button>
      )}
    </section>
  );
}

function BrokerConnectForm({
  onSubmit,
  onDone,
}: {
  onSubmit: (body: { broker: BrokerName; app_key: string; app_secret: string; account_ref: string; is_mock: boolean }) => Promise<void>;
  onDone: () => void;
}) {
  const [broker, setBroker] = useState<BrokerName>("kiwoom");
  const [appKey, setAppKey] = useState("");
  const [appSecret, setAppSecret] = useState("");
  const [accountRef, setAccountRef] = useState("");
  const [isMock, setIsMock] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  return (
    <form
      className="card mt-3 space-y-3 px-5 py-4"
      data-flow="postmortem-broker-connect"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setErr(null);
        try {
          await onSubmit({ broker, app_key: appKey, app_secret: appSecret, account_ref: accountRef, is_mock: isMock });
        } catch (error) {
          setErr(error instanceof Error ? error.message : "등록에 실패했습니다.");
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="flex gap-2">
        {(["kiwoom", "toss"] as BrokerName[]).map((b) => (
          <button
            type="button"
            key={b}
            onClick={() => setBroker(b)}
            className={`pill flat text-[13px] ${broker === b ? "!border-sky !text-sky-deep font-bold" : ""}`}
          >
            {b === "kiwoom" ? "키움증권" : "토스증권"}
          </button>
        ))}
      </div>
      <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="App Key" value={appKey} onChange={(e) => setAppKey(e.target.value)} />
      <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="App Secret" type="password" value={appSecret} onChange={(e) => setAppSecret(e.target.value)} />
      <input className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky" placeholder="계좌번호(선택, 토스는 계좌 시퀀스)" value={accountRef} onChange={(e) => setAccountRef(e.target.value)} />
      <label className="flex items-center gap-2 text-[13px] text-navy-soft">
        <input type="checkbox" checked={isMock} onChange={(e) => setIsMock(e.target.checked)} />
        모의투자 계좌 키
      </label>
      <p className="text-[12px] text-muted">
        키는 암호화되어 저장되며 다시 표시되지 않습니다. 체결 조회에만 사용됩니다.
      </p>
      {err ? <p className="text-[13px] text-red">{err}</p> : null}
      <div className="flex gap-2">
        <button type="submit" disabled={busy || !appKey || !appSecret} className="brand-grad rounded-full px-5 py-2 text-[13px] font-bold text-white disabled:opacity-60">
          {busy ? "등록 중…" : "연동"}
        </button>
        <button type="button" onClick={onDone} className="text-[13px] text-muted hover:text-navy">
          취소
        </button>
      </div>
    </form>
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
            <p className="mt-3 text-[13.5px] text-muted">이 종목의 체결내역이 없습니다. 먼저 증권사를 연동하고 동기화하세요.</p>
          )}
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
    </section>
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
