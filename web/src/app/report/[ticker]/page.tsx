"use client";

import dynamic from "next/dynamic";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import {
  listJournals,
  type Journal as JournalType,
  type ReportSource,
} from "@/lib/apiClient";
import { WatchlistButton } from "@/components/WatchlistButton";
import { EvidenceList } from "@/components/EvidenceList";
import { EvidenceTimeline } from "@/components/EvidenceTimeline";
import { MarketIndices } from "@/components/MarketIndices";
import { PrecedentSection } from "@/components/PrecedentSection";
import { ReportSideNav } from "@/components/ReportSideNav";
import { SourceIcon } from "@/components/SourceIcon";
import { SourceAgreementBar } from "@/components/SourceAgreementBar";
import { TraceCards } from "@/components/TraceCards";
import {
  dataStatusLabel,
  directionLabel,
  SOURCE_META,
  SOURCE_ORDER,
  sourceLabel,
  warningLevelLabel,
} from "@/lib/format";
import { useAuthStore } from "@/stores/authStore";
import { useJournalStore } from "@/stores/journalStore";
import { useReportStore } from "@/stores/reportStore";
import { useToastStore } from "@/stores/toastStore";

// 캔들 차트는 DOM 의존이라 SSR 비활성으로 클라이언트에서만 로드.
const StockChart = dynamic(() => import("@/components/StockChart").then((m) => m.StockChart), {
  ssr: false,
});

export default function ReportPage() {
  const params = useParams<{ ticker: string }>();
  const ticker = params.ticker;

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
  const byKey = new Map(report.sources.map((s) => [s.source, s] as const));

  return (
    <div className="relative py-8" data-page="report">
      <div className="report-aura pointer-events-none fixed inset-0 -z-10" aria-hidden="true" />

      <div className="flex gap-5 lg:gap-10">
        <ReportSideNav />

        <div className="min-w-0 flex-1">
      {/* 시장 지수 미니차트 (코스피·코스닥·원/달러·VIX) — item 7. 리포트 최상단. */}
      <MarketIndices />

      {/* 헤더 */}
      <div className="mt-4 flex flex-wrap items-start justify-between gap-3">
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
          <div className="mt-2">
            <WatchlistButton stockCode={report.stock.stock_code} />
          </div>
        </div>
      </div>

      {/* 종합 + 발행 CTA — 차트보다 위에 배치(사용자 요청). */}
      <div id="sec-summary" className="glass mt-5 flex scroll-mt-28 flex-wrap items-center gap-6 p-6">
        <div className="grid h-[120px] w-[120px] place-items-center rounded-full brand-grad text-white">
          <div className="text-center">
            <div className="text-[34px] font-extrabold leading-none">{report.score ?? "–"}</div>
            <div className="text-[12px] opacity-90">종합 점수</div>
          </div>
        </div>
        <div className="flex-1 min-w-[240px]">
          <span className={`pill ${tone(dir.tone)}`} style={{ padding: "5px 11px" }}>{dir.label}</span>
          {report.warning_level && report.warning_level !== "NORMAL" ? (
            <span
              className="pill down ml-2"
              style={{ padding: "5px 11px" }}
              title="데이터 확인이 필요한 상태입니다."
            >
              {warningLevelLabel(report.warning_level).label}
            </span>
          ) : null}
          <p className="mt-3 text-navy-soft">{report.summary ?? "요약이 없습니다."}</p>
          <p className="mt-2 text-[12px] leading-relaxed text-muted">
            <b>종합 점수</b>는 여러 데이터를 합친 <b>방향의 세기</b>(0 부정 ~ 100 긍정)이고,
            아래 <b>소스 간 일치도</b>는 <b>소스들이 방향에 얼마나 동의하는지</b>를 보여줍니다.
          </p>
        </div>
        <div className="w-full border-t border-line pt-4 text-[12.5px] text-muted">
          <span>데이터 방향성과 소스 간 일치도는 발행 시점 기준으로 확인합니다.</span>{" "}
          <Link href="/methodology" data-flow="methodology-link" className="font-semibold text-sky-deep">
            방법론 보기
          </Link>
        </div>
      </div>

      {/* 주가 차트 + 시세 (분/일/월/년) — 종합 점수 아래로 이동. */}
      <div id="sec-chart" className="mt-5 scroll-mt-28">
        <StockChart stockCode={report.stock.stock_code} stockName={report.stock.stock_name} />
      </div>

      {/* 소스별 상세 근거 — 소스 간 일치도보다 위에 배치(사용자 요청) */}
      <div id="sec-sources" className="scroll-mt-28">
        <h2 className="mt-8 text-[18px] font-bold">소스별 상세 근거</h2>
        <div className="mt-4 grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {SOURCE_ORDER.map((key) => {
            const src = byKey.get(key);
            return (
              <SourceCard
                key={key}
                sourceKey={key}
                src={src}
                ticker={ticker}
              />
            );
          })}
        </div>
      </div>

      {/* 소스 간 일치도 + 긍정/주의 근거 (근거 중심 §5.3). 리포트 전체 공개(#788). */}
      <div id="sec-agreement" className="scroll-mt-28">
        <SourceAgreementBar agreement={report.source_agreement} sources={report.sources} />
      </div>
      <div id="sec-evidence" className="scroll-mt-28">
        <EvidenceList
          title="긍정 방향 근거"
          tone="up"
          lead={report.bull_point}
          items={report.positive_evidence ?? []}
          emptyText="현재 긍정 방향으로 확인된 근거가 없습니다."
        />
        <EvidenceList
          title="주의 근거"
          tone="down"
          lead={report.bear_point}
          items={report.caution_evidence ?? []}
          emptyText="현재 특별히 주의할 근거는 확인되지 않았습니다."
        />
        {report.warning_level && report.warning_level !== "NORMAL" && (
          <section className="glass mt-4 p-5" data-section="needs-review">
            <h2 className="text-[16px] font-bold">추가 확인 필요</h2>
            <p className="mt-1 text-[13px] text-navy-soft">
              이 종목은 <b>{warningLevelLabel(report.warning_level).label}</b> 상태로, 소스 간
              방향이 엇갈리거나 일부 데이터가 아직 부족합니다. 위 소스별 상세 근거를 함께
              확인하고, 필요하면 저널에 “추가 확인 필요”로 기록해 두면 이후 복기에 도움이 됩니다.
            </p>
          </section>
        )}
      </div>
      {/* S5 수요·확장 흔적 카드 */}
      <div id="sec-trace" className="scroll-mt-28">
        <TraceCards sources={report.sources} />
      </div>

      {/* S2 근거 타임라인 — 소스 교차 시간순 근거. */}
      <div id="sec-timeline" className="scroll-mt-28">
        <EvidenceTimeline stockCode={report.stock.stock_code} />
      </div>

      {/* S6 과거 유사 사례 — 같은 소스·방향 신호 뒤 20일 실측 성과(참고용). */}
      <div id="sec-precedent" className="scroll-mt-28">
        <PrecedentSection stockCode={report.stock.stock_code} />
      </div>

      {/* 저널 저장 — 발행 리포트(final_signal_id)에 판단을 기록. */}
      {report.report_version?.final_signal_id != null && (
        <div id="sec-journal" className="scroll-mt-28">
          <JournalSaveCard
            stockCode={report.stock.stock_code}
            finalSignalId={report.report_version.final_signal_id}
          />
        </div>
      )}

          <p className="mt-6 rounded-[12px] bg-surface-2 p-4 text-[12.5px] text-muted">{report.notice}</p>
        </div>
      </div>
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
}: {
  sourceKey: "price" | "dart" | "hiring" | "datalab" | "patent" | "report";
  src: ReportSource | undefined;
  ticker: string;
}) {
  const meta = SOURCE_META[sourceKey];
  if (!src) {
    return (
      <div
        data-source={sourceKey}
        className="glass grid min-h-[140px] place-items-center p-5 text-center"
      >
        <div className="flex items-center gap-1.5 font-bold text-muted">
          <SourceIcon source={sourceKey} size={18} /> {meta.label}
        </div>
        <div className="text-[12.5px] text-muted">데이터가 아직 없습니다.</div>
      </div>
    );
  }
  const dir = directionLabel(src.direction);
  const hasScore = (src.data_status === "ok" || src.data_status === "partial") && src.score != null;
  return (
    <Link href={`/report/${encodeURIComponent(ticker)}/${sourceKey}`} className="glass hover-lift block p-5">
      <div className="flex items-center gap-2 font-bold">
        <SourceIcon source={sourceKey} size={18} className="text-navy-soft" /> {meta.label}
        <span className={`pill ${tone(dir.tone)} ml-auto`} style={{ padding: "3px 9px", fontSize: 12 }}>{dir.label}</span>
      </div>
      {hasScore ? (
        <div className="mt-3 flex items-baseline gap-1">
          <span className="text-[36px] font-extrabold leading-none">{src.score}</span>
          <span className="text-[14px] font-semibold text-muted">/100</span>
        </div>
      ) : (
        <div className="mt-3 text-[16px] font-bold text-muted">{dataStatusLabel(src.data_status) || "—"}</div>
      )}
      <p className="mt-2 min-h-[38px] text-[13.5px] text-navy-soft">{src.summary ?? (src.data_status === "no_signal" ? "시그널이 없습니다." : sourceLabel(sourceKey) + " 데이터 요약")}</p>
      <div className="mt-1 text-[13px] font-semibold text-sky-deep">상세 보기 →</div>
    </Link>
  );
}

function tone(t: "up" | "down" | "flat"): string {
  return t === "up" ? "up" : t === "down" ? "down" : "flat";
}
