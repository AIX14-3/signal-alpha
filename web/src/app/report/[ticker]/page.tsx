"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  listJournals,
  type Journal as JournalType,
  type PredictionRate,
  type ReportSource,
} from "@/lib/apiClient";
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
import { useJournalStore } from "@/stores/journalStore";
import { useReportStore } from "@/stores/reportStore";
import { useToastStore } from "@/stores/toastStore";

export default function ReportPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = params.ticker;
  const router = useRouter();

  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const { report, loading, error, load } = useReportStore();

  useEffect(() => {
    // 리포트(/api/reports/{code})는 공개 엔드포인트라 토큰이 없으면 401 이 아니라
    // 200 + is_member=false(익명 뷰)를 돌려준다 → apiFetch 의 401 자동복구가 작동하지 않는다.
    // 따라서 인메모리 access 토큰이 비어 있는 새로고침 직후 hydrate 완료 전에 로드하면
    // 로그인 상태인데도 비회원으로 굳어진다. 인증 hydrate 가 확정(authenticated/anonymous)된
    // 뒤에 로드해 토큰이 실리도록 한다.
    if (status === "idle" || status === "loading") return;
    void load(ticker);
  }, [ticker, load, status]);

  // hydrate 가 끝나기 전(idle/loading)에는 아직 로드를 시작하지 않으므로 로딩 표시를 유지한다.
  if (loading || status === "idle" || status === "loading")
    return <p className="py-16 text-center text-muted">리포트를 불러오는 중…</p>;
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
  // 전체 리포트는 구독자만 열람. 비구독 회원은 구독(/pricing), 비회원은 로그인으로 보낸다.
  const onUnlock = () => {
    router.push(isMember ? "/pricing" : loginHref);
  };

  return (
    <div className="py-10" data-page="report">
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
          {user && unlocked && (
            <span className="pill" style={{ background: "rgba(14,165,233,.12)", color: "#0284c7", padding: "5px 11px" }}>
              구독 중
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
                <Link href="/pricing" data-flow="unlock-report-pricing" className="brand-grad inline-block rounded-full px-6 py-3 text-[15px] font-bold text-white">
                  구독하고 전체 리포트 열람
                </Link>
              ) : (
                <Link href={loginHref} data-flow="unlock-report-login" className="brand-grad inline-block rounded-full px-6 py-3 text-[15px] font-bold text-white">
                  로그인하고 구독하기
                </Link>
              )}
            </div>
          </div>
        )}
        <div className="w-full border-t border-line pt-4 text-[12.5px] text-muted">
          <span>데이터 방향성과 소스 간 일치도는 발행 시점 기준으로 확인합니다.</span>{" "}
          <Link href="/methodology" data-flow="methodology-link" className="font-semibold text-sky-deep">
            방법론 보기
          </Link>
        </div>
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
              onUnlock={onUnlock}
            />
          );
        })}
      </div>

      {/* 저널 저장 — 구독자가 열람한 리포트(final_signal_id)에 판단을 기록. */}
      {unlocked && report.report_version?.final_signal_id != null && (
        <JournalSaveCard
          stockCode={report.stock.stock_code}
          finalSignalId={report.report_version.final_signal_id}
        />
      )}

      <p className="mt-6 rounded-[12px] bg-surface-2 p-4 text-[12.5px] text-muted">{report.notice}</p>
    </div>
  );
}

const JOURNAL_VIEWS: [string, string][] = [
  ["watch", "계속 관찰"],
  ["research_more", "추가 확인 필요"],
  ["not_relevant", "낮은 관련도"],
];
const JOURNAL_VIEW_LABEL: Record<string, string> = Object.fromEntries(JOURNAL_VIEWS);

/** 이 종목에 이미 남긴 내 저널 이력 — 중복 저장 전에 과거 판단을 보여준다. */
function JournalHistory({ items }: { items: JournalType[] }) {
  if (items.length === 0) return null;
  return (
    <div className="mb-4 rounded-[12px] bg-surface-2 p-3" data-flow="journal-history">
      <div className="flex items-center justify-between">
        <span className="text-[12.5px] font-bold">이 종목의 내 저널 {items.length}건</span>
        <Link href="/mypage" className="text-[12px] font-semibold text-sky-deep">
          전체 보기 →
        </Link>
      </div>
      <ul className="mt-1.5 space-y-1 text-[12.5px]">
        {items.slice(0, 3).map((j) => (
          <li key={j.journal_id} className="flex flex-wrap items-baseline gap-x-2">
            <span className="text-muted">{j.created_at?.slice(0, 10)}</span>
            <span className="pill flat" style={{ padding: "1px 7px", fontSize: 11 }}>
              {JOURNAL_VIEW_LABEL[j.user_view] ?? j.user_view}
            </span>
            {j.memo && <span className="truncate text-navy-soft">{j.memo}</span>}
          </li>
        ))}
      </ul>
    </div>
  );
}

function JournalSaveCard({ stockCode, finalSignalId }: { stockCode: string; finalSignalId: number }) {
  const createJournal = useJournalStore((s) => s.create);
  const showToast = useToastStore((s) => s.show);
  const [userView, setUserView] = useState("watch");
  const [memo, setMemo] = useState("");
  const [tags, setTags] = useState("");
  const [busy, setBusy] = useState(false);
  const [saved, setSaved] = useState(false);
  const [history, setHistory] = useState<JournalType[]>([]);

  useEffect(() => {
    // 이 카드가 렌더링되는 시점 = 구독자 언락 상태라 저널 API 호출 가능.
    listJournals({ stock_code: stockCode })
      .then((d) => setHistory(d.items))
      .catch(() => setHistory([]));
  }, [stockCode]);

  async function save() {
    setBusy(true);
    try {
      const created = await createJournal({
        stock_code: stockCode,
        final_signal_id: finalSignalId,
        user_view: userView,
        memo: memo.trim() || undefined,
        tags: tags.split(",").map((t) => t.trim()).filter(Boolean),
      });
      setHistory([created, ...history]);
      setSaved(true);
      showToast("저널에 저장했습니다.", "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  if (saved)
    return (
      <div className="card mt-6 p-5" data-flow="journal-save">
        <JournalHistory items={history} />
        <div className="flex items-center justify-between">
          <p className="text-[14px] font-semibold">이 리포트를 저널에 저장했습니다.</p>
          <Link href="/mypage" className="text-[13.5px] font-semibold text-sky-deep">
            마이페이지 저널 보기 →
          </Link>
        </div>
      </div>
    );

  return (
    <section className="card mt-6 p-5" data-flow="journal-save">
      <JournalHistory items={history} />
      <h2 className="text-[16px] font-bold">이 리포트를 저널에 저장</h2>
      <p className="mt-1 text-[12.5px] text-muted">
        지금 판단을 기록해 두면, 이후 실제 주가 변동(7·30거래일)과 함께 복기할 수 있습니다. 매수·매도 판단이 아닌 관찰 기록입니다.
      </p>
      <div className="mt-3 flex flex-wrap gap-2">
        {JOURNAL_VIEWS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setUserView(key)}
            className={`pill flat text-[12.5px] ${userView === key ? "!border-sky !text-sky-deep font-bold" : ""}`}
            style={{ padding: "4px 10px" }}
          >
            {label}
          </button>
        ))}
      </div>
      <textarea
        value={memo}
        onChange={(e) => setMemo(e.target.value)}
        rows={2}
        className="card mt-3 w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky"
        placeholder="메모 (선택)"
      />
      <div className="mt-2 flex flex-wrap items-center gap-2">
        <input
          value={tags}
          onChange={(e) => setTags(e.target.value)}
          className="card min-w-[220px] flex-1 px-4 py-2.5 text-[13px] outline-none focus:border-sky"
          placeholder="태그 (쉼표로 구분, 최대 10개)"
        />
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy}
          className="brand-grad rounded-full px-5 py-2 text-[13.5px] font-bold text-white disabled:opacity-60"
        >
          {busy ? "저장 중…" : "저널에 저장"}
        </button>
      </div>
    </section>
  );
}

function SourceCard({
  sourceKey,
  src,
  ticker,
  isMember,
  onUnlock,
}: {
  sourceKey: "price" | "dart" | "hiring" | "datalab" | "patent" | "report";
  src: ReportSource | undefined;
  ticker: string;
  isMember: boolean;
  onUnlock: () => void;
}) {
  const meta = SOURCE_META[sourceKey];
  if (!src || src.locked) {
    return (
      <button
        type="button"
        onClick={onUnlock}
        data-flow="unlock-source"
        data-source={sourceKey}
        className="card relative grid min-h-[140px] place-items-center overflow-hidden p-5 text-center disabled:opacity-70"
      >
        <div className="font-bold">{meta.icon} {meta.label}</div>
        <div className="absolute inset-0 grid place-items-center bg-surface/60 backdrop-blur-[6px]">
          <div className="text-[22px]">🔒</div>
          <div className="text-[12.5px] font-semibold text-navy-soft">
            {isMember ? "구독하고 열람" : "로그인하고 열람"}
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
  onUnlock,
}: {
  sourceKey: "price" | "dart" | "datalab" | "hiring" | "patent" | "report";
  rate: PredictionRate | undefined;
  isMember: boolean;
  onUnlock: () => void;
}) {
  const meta = SOURCE_META[sourceKey];
  if (!rate || rate.locked) {
    return (
      <button
        type="button"
        onClick={onUnlock}
        data-flow="unlock-prediction-rate"
        data-source={sourceKey}
        className="card relative grid min-h-[104px] place-items-center overflow-hidden p-3 text-center disabled:opacity-70"
      >
        <div className="text-[13px] font-bold">{meta.icon} {meta.label}</div>
        <div className="absolute inset-0 grid place-items-center bg-surface/60 backdrop-blur-[6px]">
          <div className="text-[18px]">🔒</div>
          <div className="text-[11.5px] font-semibold text-navy-soft">
            {isMember ? "구독" : "로그인"}
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
