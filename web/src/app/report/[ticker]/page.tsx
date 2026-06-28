"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect } from "react";
import { ApiError, type PredictionRate, type ReportSource } from "@/lib/apiClient";
import { WatchlistButton } from "@/components/WatchlistButton";
import {
  directionLabel,
  PREDICTION_RATE_ORDER,
  SOURCE_META,
  SOURCE_ORDER,
  sourceLabel,
  sourceStatusLine,
} from "@/lib/format";
import { useAuthStore } from "@/stores/authStore";
import { useReportStore } from "@/stores/reportStore";
import { useToastStore } from "@/stores/toastStore";

export default function ReportPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = params.ticker;
  const router = useRouter();

  const user = useAuthStore((s) => s.user);
  const { report, quota, loading, issuing, error, load, issue, loadQuota } = useReportStore();
  const showToast = useToastStore((s) => s.show);

  useEffect(() => {
    void load(ticker);
  }, [ticker, load]);

  useEffect(() => {
    if (user) void loadQuota();
  }, [user, loadQuota]);

  async function onIssue() {
    try {
      await issue(ticker);
      await loadQuota();
    } catch (err) {
      if (err instanceof ApiError && err.status === 402) {
        showToast("무료 열람을 모두 사용했습니다. 구독하면 무제한으로 볼 수 있어요.", "info");
        router.push("/pricing");
        return;
      }
      showToast((err as Error).message, "error");
    }
  }

  if (loading) return <p className="py-16 text-center text-muted">리포트를 불러오는 중…</p>;
  if (error && !report)
    return (
      <div className="py-16 text-center">
        <p className="text-red">{error}</p>
        <Link href="/" className="mt-3 inline-block text-sky-deep">← 다른 종목 검색</Link>
      </div>
    );
  if (!report) return null;

  const dir = directionLabel(report.direction);
  const unlocked = report.access.unlocked;
  const isMember = report.access.is_member;
  const byKey = new Map(report.sources.map((s) => [s.source, s] as const));
  const byPred = new Map((report.prediction_rates ?? []).map((p) => [p.source, p] as const));
  const loginHref = `/login?returnTo=${encodeURIComponent(`/report/${ticker}`)}`;
  const issueLabel = quota?.subscription_active
    ? "리포트 열람"
    : quota
      ? `무료로 열람하기 (${quota.free_remaining}회 남음)`
      : "리포트 발행(열람)";
  const onUnlock = () => {
    if (isMember) void onIssue();
    else router.push(loginHref);
  };

  return (
    <div className="py-10">
      {/* 헤더 */}
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="text-[13px] text-muted">
            {report.stock.stock_code} · {report.stock.market ?? "—"} · {report.stock.sector ?? "—"}
          </div>
          <h1 className="my-1 text-[32px] font-extrabold">{report.stock.stock_name} 리포트</h1>
          {report.report_version?.updated_at && (
            <span className="text-[12px] text-muted">업데이트 {report.report_version.updated_at.slice(0, 16).replace("T", " ")}</span>
          )}
        </div>
        <div className="text-right">
          {user && quota && (
            <span className="pill" style={{ background: "rgba(14,165,233,.12)", color: "#0284c7", padding: "5px 11px" }}>
              {quota.subscription_active ? "구독 중 · 무제한" : `무료 ${quota.free_remaining}회 남음`}
            </span>
          )}
          <div className="mt-2">
            <WatchlistButton stockCode={report.stock.stock_code} />
          </div>
        </div>
      </div>

      {/* 종합 + 발행 CTA */}
      <div className="card mt-5 flex flex-wrap items-center gap-6 p-6">
        {unlocked ? (
          <>
            <div className="grid h-[120px] w-[120px] place-items-center rounded-full brand-grad text-white">
              <div className="text-center">
                <div className="text-[34px] font-extrabold leading-none">{report.score ?? "–"}</div>
                <div className="text-[12px] opacity-90">종합 점수</div>
              </div>
            </div>
            <div className="flex-1 min-w-[240px]">
              <span className={`pill ${tone(dir.tone)}`} style={{ padding: "5px 11px" }}>{dir.label}</span>
              {report.ml_return?.direction ? (
                <span className="ml-2 inline-flex items-center gap-1 text-[12px] text-muted">
                  <span>ML 수익률</span>
                  <span
                    className={`pill ${tone(directionLabel(report.ml_return.direction).tone)}`}
                    style={{ padding: "3px 9px" }}
                  >
                    {directionLabel(report.ml_return.direction).label}
                  </span>
                  {report.ml_return.confidence != null ? (
                    <span>신뢰도 {Math.round(report.ml_return.confidence * 100)}%</span>
                  ) : null}
                </span>
              ) : null}
              <p className="mt-3 text-navy-soft">{report.summary ?? "요약이 없습니다."}</p>
            </div>
          </>
        ) : (
          <div className="flex-1">
            <h2 className="text-[18px] font-bold">전체 리포트가 잠겨 있어요</h2>
            <p className="mt-1 text-[14px] text-muted">{report.notice}</p>
            <div className="mt-4">
              {isMember ? (
                <button onClick={() => void onIssue()} disabled={issuing} className="brand-grad rounded-full px-6 py-3 text-[15px] font-bold text-white disabled:opacity-60">
                  {issuing ? "열람 중…" : issueLabel}
                </button>
              ) : (
                <Link href={loginHref} className="brand-grad inline-block rounded-full px-6 py-3 text-[15px] font-bold text-white">
                  로그인하고 무료 3회 열람
                </Link>
              )}
            </div>
          </div>
        )}
      </div>

      {/* AI 예측률 — 주가 BASE ⊕ 각 공공데이터로 만든 0–100 예측 점수(통합은 위 종합 점수). */}
      {byPred.size > 0 && (
        <section className="mt-8">
          <h2 className="text-[18px] font-bold">AI 예측률</h2>
          <p className="mt-1 text-[13px] text-muted">
            주가 예측을 기준으로 각 공공데이터를 가·감산한 소스별 예측 점수입니다(0–100, 50=중립). 통합 예측은 위 종합 점수예요.
          </p>
          <div className="mt-4 grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
            {PREDICTION_RATE_ORDER.map((key) => (
              <PredictionRateCard
                key={key}
                sourceKey={key}
                rate={byPred.get(key)}
                isMember={isMember}
                busy={issuing}
                onUnlock={onUnlock}
              />
            ))}
          </div>
        </section>
      )}

      {/* 5소스 카드 */}
      <div className="mt-6 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {SOURCE_ORDER.map((key) => {
          const src = byKey.get(key);
          return (
            <SourceCard
              key={key}
              sourceKey={key}
              src={src}
              ticker={ticker}
              isMember={isMember}
              busy={issuing}
              onUnlock={onUnlock}
            />
          );
        })}
      </div>

      <p className="mt-6 rounded-[12px] bg-surface-2 p-4 text-[12.5px] text-muted">{report.notice}</p>
    </div>
  );
}

function SourceCard({
  sourceKey,
  src,
  ticker,
  isMember,
  busy,
  onUnlock,
}: {
  sourceKey: "price" | "dart" | "hiring" | "datalab" | "report";
  src: ReportSource | undefined;
  ticker: string;
  isMember: boolean;
  busy: boolean;
  onUnlock: () => void;
}) {
  const meta = SOURCE_META[sourceKey];
  if (!src || src.locked) {
    return (
      <button
        type="button"
        onClick={onUnlock}
        disabled={busy}
        className="card relative grid min-h-[140px] place-items-center overflow-hidden p-5 text-center disabled:opacity-70"
      >
        <div className="font-bold">{meta.icon} {meta.label}</div>
        <div className="absolute inset-0 grid place-items-center bg-surface/60 backdrop-blur-[6px]">
          <div className="text-[22px]">🔒</div>
          <div className="text-[12.5px] font-semibold text-navy-soft">
            {busy ? "열람 중…" : isMember ? "클릭해서 무료 열람" : "로그인하고 열람"}
          </div>
        </div>
      </button>
    );
  }
  const dir = directionLabel(src.direction);
  return (
    <Link href={`/report/${encodeURIComponent(ticker)}/${sourceKey}`} className="card block p-5 transition hover:shadow-lg">
      <div className="flex items-center gap-2 font-bold">
        {meta.icon} {meta.label}
        <span className={`pill ${tone(dir.tone)} ml-auto`} style={{ padding: "3px 9px", fontSize: 12 }}>{dir.label}</span>
      </div>
      <p className="mt-2 min-h-[38px] text-[13.5px] text-navy-soft">{src.summary ?? (src.data_status === "no_signal" ? "시그널이 없습니다." : sourceLabel(sourceKey) + " 데이터 요약")}</p>
      <div className="mt-2 text-[12px] text-muted">{sourceStatusLine(src.data_status, src.score)}</div>
      <div className="mt-2 text-[13px] font-semibold text-sky-deep">상세 보기 →</div>
    </Link>
  );
}

function PredictionRateCard({
  sourceKey,
  rate,
  isMember,
  busy,
  onUnlock,
}: {
  sourceKey: "price" | "dart" | "datalab" | "hiring" | "patent" | "report";
  rate: PredictionRate | undefined;
  isMember: boolean;
  busy: boolean;
  onUnlock: () => void;
}) {
  const meta = SOURCE_META[sourceKey];
  if (!rate || rate.locked) {
    return (
      <button
        type="button"
        onClick={onUnlock}
        disabled={busy}
        className="card relative grid min-h-[104px] place-items-center overflow-hidden p-3 text-center disabled:opacity-70"
      >
        <div className="text-[13px] font-bold">{meta.icon} {meta.label}</div>
        <div className="absolute inset-0 grid place-items-center bg-surface/60 backdrop-blur-[6px]">
          <div className="text-[18px]">🔒</div>
          <div className="text-[11.5px] font-semibold text-navy-soft">
            {busy ? "열람 중…" : isMember ? "클릭해 열람" : "로그인"}
          </div>
        </div>
      </button>
    );
  }
  const dir = directionLabel(rate.direction);
  const hasScore = rate.data_status !== "missing" && rate.score != null;
  return (
    <div className="card p-3 text-center">
      <div className="text-[13px] font-bold">{meta.icon} {meta.label}</div>
      {hasScore ? (
        <>
          <div className="mt-1 text-[26px] font-extrabold leading-none">{rate.score}</div>
          <span className={`pill ${tone(dir.tone)} mt-2 inline-block`} style={{ padding: "2px 8px", fontSize: 11 }}>
            {dir.label}
          </span>
        </>
      ) : (
        <div className="mt-3 text-[12px] text-muted">데이터 수집 전</div>
      )}
    </div>
  );
}

function tone(t: "up" | "down" | "flat"): string {
  return t === "up" ? "up" : t === "down" ? "down" : "flat";
}
