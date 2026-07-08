"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  cancelPayment,
  checkout,
  confirmPayment,
  createPost,
  deleteMe,
  getMySubscription,
  paymentHistory,
  refundPayment,
  resumePayment,
  updateMe,
  type Journal,
  type PaymentHistoryItem,
  type Plan,
  type Subscription,
} from "@/lib/apiClient";
import { won } from "@/lib/format";
import { pay } from "@/lib/portone";
import { isSocialDevMode, SOCIAL_PROVIDERS, socialAuthCode, startSocialOAuth } from "@/lib/social";
import {
  hasRetrospective,
  JournalChartPanel,
  JournalTimelinePanel,
  RetrospectiveBlock,
} from "@/components/JournalChart";
import { useAuthStore } from "@/stores/authStore";
import { useJournalStore } from "@/stores/journalStore";
import { useSocialStore } from "@/stores/socialStore";
import { useWatchlistStore } from "@/stores/watchlistStore";
import { useToastStore } from "@/stores/toastStore";

type Tab = "watchlist" | "subscription" | "journal" | "social" | "profile";

const TABS: [Tab, string][] = [
  ["watchlist", "관심종목"],
  ["subscription", "구독"],
  ["journal", "저널"],
  ["social", "소셜 연동"],
  ["profile", "회원정보"],
];

export default function MyPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const [tab, setTab] = useState<Tab>("watchlist");
  const journalItems = useJournalStore((s) => s.items);
  const loadJournals = useJournalStore((s) => s.load);

  // 복기 알림 배지용 — 변동이 확정됐는데 아직 회고가 없는 저널 수.
  useEffect(() => {
    if (status === "authenticated" && user?.subscription_active) void loadJournals();
  }, [status, user?.subscription_active, loadJournals]);
  const pendingRetro = journalItems.filter(isRetroPending).length;

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated" || !user) {
    return <p className="py-16 text-center text-muted">로그인 정보를 확인하는 중…</p>;
  }

  return (
    <div className="py-12" data-page="mypage">
      <h1 className="text-[32px] font-extrabold">
        마이페이지 <span className="pill flat align-middle text-[13px]" style={{ padding: "3px 9px" }}>{user.member_code}</span>
      </h1>
      {/* 매매 부검·방법론은 상단 메뉴에서 마이 안으로 이동(배선 이전, 페이지 코드는 유지). */}
      <div className="mt-4 flex flex-wrap gap-2" data-flow="mypage-tools">
        <Link href="/postmortem" className="pill flat text-[13.5px]" style={{ padding: "6px 14px" }}>
          매매 부검
        </Link>
        <Link href="/methodology" className="pill flat text-[13.5px]" style={{ padding: "6px 14px" }}>
          방법론
        </Link>
      </div>
      <div className="mt-6 flex flex-wrap gap-2 border-b border-line">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            data-tab={key}
            className={`-mb-px border-b-2 px-4 py-3 text-[14.5px] font-semibold ${
              tab === key ? "border-sky text-navy" : "border-transparent text-muted"
            }`}
          >
            {label}
            {key === "journal" && pendingRetro > 0 && (
              <span
                className="pill up ml-1 align-middle"
                style={{ padding: "1px 7px", fontSize: 11 }}
                data-flow="journal-retro-alert"
                title={`복기할 저널 ${pendingRetro}건`}
              >
                {pendingRetro}
              </span>
            )}
          </button>
        ))}
      </div>

      <div className="mt-6">
        {tab === "watchlist" && <WatchlistTab />}
        {tab === "subscription" && <SubscriptionTab />}
        {tab === "journal" && <JournalTab />}
        {tab === "social" && <SocialTab />}
        {tab === "profile" && <ProfileTab />}
      </div>
    </div>
  );
}

function WatchlistTab() {
  const { items, count, loading, error, load, remove } = useWatchlistStore();
  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <p className="text-muted" data-panel="watchlist">불러오는 중…</p>;
  if (error) return <p className="text-red" data-panel="watchlist">{error}</p>;
  if (items.length === 0)
    return (
      <p className="text-muted" data-panel="watchlist">
        관심종목이 없습니다. <Link href="/" className="text-sky-deep">종목 검색하기</Link>
      </p>
    );

  return (
    <div data-panel="watchlist">
      <p className="mb-3 text-[13px] text-muted">{count}개 등록 (무제한)</p>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.stock.id} className="card flex items-center justify-between px-5 py-4">
            <Link href={`/report/${item.stock.stock_code}`} className="font-bold hover:text-sky-deep">
              {item.stock.stock_name}
              <span className="ml-2 text-[13px] font-normal text-muted">{item.stock.stock_code}</span>
            </Link>
            <button type="button" onClick={() => void remove(item.stock.stock_code)} className="text-[13px] font-semibold text-muted hover:text-red">
              삭제
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}

function SubscriptionTab() {
  const [subscription, setSubscription] = useState<Subscription | null>(null);
  const [plan, setPlan] = useState<Plan | null>(null);
  const [payments, setPayments] = useState<PaymentHistoryItem[]>([]);
  const [busy, setBusy] = useState(false);
  const showToast = useToastStore((s) => s.show);
  const refreshMe = useAuthStore((s) => s.refreshMe);

  async function reload() {
    const [sub, history] = await Promise.all([getMySubscription(), paymentHistory()]);
    setSubscription(sub.subscription);
    setPlan(sub.plan);
    setPayments(history.items);
  }
  useEffect(() => {
    reload().catch((err) => showToast((err as Error).message, "error"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function subscribe() {
    setBusy(true);
    try {
      const info = await checkout();
      const payment_id = await pay({
        paymentId: info.payment_id,
        orderName: info.order_name,
        amount: info.amount,
        customerEmail: info.customer.email ?? undefined,
        customerName: info.customer.full_name ?? undefined,
        customerPhone: info.customer.phone_number ?? undefined,
      });
      await confirmPayment({ payment_id });
      await reload();
      await refreshMe();
      showToast("구독이 시작되었습니다.", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function cancel() {
    const ok = window.confirm(
      "구독을 해지하시겠어요?\n\n만료일까지는 그대로 이용할 수 있으며, 이후 자동으로 종료됩니다.\n환불은 제공되지 않습니다.",
    );
    if (!ok) return;
    setBusy(true);
    try {
      const res = await cancelPayment();
      await reload();
      await refreshMe();
      const until = res.expires_at ? ` ${res.expires_at.slice(0, 10)}까지 이용 가능합니다.` : "";
      showToast(`구독 해지가 예약되었습니다.${until}`, "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function resume() {
    setBusy(true);
    try {
      await resumePayment();
      await reload();
      await refreshMe();
      showToast("구독을 계속 이용합니다. 해지 예약이 취소되었습니다.", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function refund() {
    const ok = window.confirm(
      "환불을 요청하시겠어요?\n\n결제 후 7일 이내는 전액, 이후에는 남은 기간만큼 일할 계산되어 환불됩니다.\n환불 시 구독은 즉시 해지됩니다.",
    );
    if (!ok) return;
    setBusy(true);
    try {
      const res = await refundPayment();
      await reload();
      await refreshMe();
      showToast(`환불이 접수되었습니다. ${won(res.amount)} 환불 예정입니다.`, "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  const active = subscription?.status === "active";
  const cancelScheduled = active && Boolean(subscription?.cancelled_at);
  const expiringSoon = active && Boolean(subscription?.expiring_soon);
  const yearly = subscription?.billing_cycle === "yearly";
  const monthlyPrice = plan?.price_monthly && plan.price_monthly > 0 ? plan.price_monthly : 9900;
  const yearlyPrice = plan?.price_yearly && plan.price_yearly > 0 ? plan.price_yearly : 99000;
  // 남은 기간을 사람이 읽기 쉬운 단위로(연간권은 "약 1년"/"약 N개월"). days_remaining 은 /me 가 내려줌.
  const daysLeft = subscription?.days_remaining ?? null;
  const remainingLabel =
    daysLeft == null
      ? null
      : daysLeft >= 330
        ? "약 1년"
        : daysLeft >= 60
          ? `약 ${Math.round(daysLeft / 30)}개월`
          : `${daysLeft}일`;

  return (
    <div className="card max-w-[480px] p-7" data-panel="subscription">
      {expiringSoon && (
        <div className="mb-5 rounded-[12px] bg-surface-2 px-4 py-3 text-[13px] text-navy-soft">
          {cancelScheduled
            ? `구독이 ${subscription?.days_remaining ?? 0}일 후 종료됩니다. 계속 이용하려면 ‘구독 계속하기’를 눌러 주세요.`
            : `구독이 ${subscription?.days_remaining ?? 0}일 후 만료됩니다. 이용 공백 없이 ‘지금 연장하기’로 갱신하세요.`}
        </div>
      )}
      <div className="text-[13px] font-bold uppercase tracking-[0.1em] text-sky-deep">현재 상태</div>
      <div className="mt-2 text-[28px] font-extrabold">
        {cancelScheduled ? "해지 예약됨" : active ? (yearly ? "연 구독 중" : "월 구독 중") : "무료"}
      </div>
      <div className="mt-1 text-[14px] text-muted">
        {active && subscription?.expires_at
          ? cancelScheduled
            ? `만료 ${subscription.expires_at.slice(0, 10)}까지 이용 가능 (이후 종료)`
            : `만료 ${subscription.expires_at.slice(0, 10)}${remainingLabel ? ` · 남은 기간 ${remainingLabel}` : ""}`
          : "비구독 회원은 DART·네이버 데이터만 볼 수 있어요. 구독 시 전체 리포트가 열립니다."}
      </div>
      <div className="mt-3 text-[15px]">
        {active && yearly
          ? `${won(yearlyPrice)} /년 · 무제한 열람`
          : `${won(monthlyPrice)} /월 · 무제한 열람`}
      </div>
      <div className="mt-6 flex flex-wrap gap-2">
        {active ? (
          cancelScheduled ? (
            <button type="button" onClick={() => void resume()} disabled={busy} data-flow="subscription-resume" className="brand-grad rounded-full px-6 py-2.5 text-[14px] font-bold text-white disabled:opacity-60">
              {busy ? "처리 중…" : "구독 계속하기"}
            </button>
          ) : (
            <>
              {expiringSoon && (
                <button type="button" onClick={() => void subscribe()} disabled={busy} data-flow="subscription-renew" className="brand-grad rounded-full px-6 py-2.5 text-[14px] font-bold text-white disabled:opacity-60">
                  {busy ? "처리 중…" : "지금 연장하기"}
                </button>
              )}
              <button type="button" onClick={() => void cancel()} disabled={busy} data-flow="subscription-cancel" className="rounded-full border border-line px-5 py-2.5 text-[14px] font-semibold text-navy-soft hover:border-red hover:text-red disabled:opacity-60">
                구독 취소
              </button>
            </>
          )
        ) : (
          <button type="button" onClick={() => void subscribe()} disabled={busy} data-flow="subscription-start" className="brand-grad rounded-full px-6 py-2.5 text-[14px] font-bold text-white disabled:opacity-60">
            {busy ? "처리 중…" : "월 9,900원 구독하기"}
          </button>
        )}
        <Link href="/pricing" className="rounded-full border border-line px-5 py-2.5 text-[14px] font-semibold text-navy-soft hover:text-navy">
          요금 안내
        </Link>
      </div>
      {active && (
        <button type="button" onClick={() => void refund()} disabled={busy} data-flow="subscription-refund" className="mt-3 text-[13px] font-semibold text-muted hover:text-red disabled:opacity-60">
          환불 요청 (즉시 해지)
        </button>
      )}

      {payments.length > 0 && (
        <div className="mt-7 border-t border-line pt-5">
          <div className="text-[13px] font-bold text-navy-soft">결제 내역</div>
          <ul className="mt-3 space-y-2">
            {/* 같은 payment_id 가 결제/취소 두 행으로 올 수 있어 인덱스를 섞어 키를 유일하게 만든다. */}
            {payments.map((p, index) => (
              <li key={`${p.payment_id}-${index}`} className="flex items-center justify-between text-[13.5px]">
                <span className="text-muted">{p.paid_at ? p.paid_at.slice(0, 10) : "—"}</span>
                <span className="font-semibold">{(p.order_name ?? "구독").replace("Signal Alpha ", "")} · {won(p.amount)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

const JOURNAL_VIEWS: [string, string][] = [
  ["watch", "계속 관찰"],
  ["research_more", "추가 확인 필요"],
  ["not_relevant", "낮은 관련도"],
];
const JOURNAL_VIEW_LABEL: Record<string, string> = Object.fromEntries(JOURNAL_VIEWS);
const HORIZON_LABEL: Record<string, string> = { "7td": "7거래일", "30td": "30거래일" };

// 복기 대기 — 변동은 확정됐는데 아직 회고가 없는 저널.
// hasRetrospective 는 JournalChart 에서 단일 정의를 import(카드 표시와 정의 일치).
function isRetroPending(j: Journal): boolean {
  return j.outcomes.length > 0 && !hasRetrospective(j);
}

function JournalTab() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const { items, loading, error, load, update, remove } = useJournalStore();
  const showToast = useToastStore((s) => s.show);
  const [editingId, setEditingId] = useState<number | null>(null);
  const [editView, setEditView] = useState("watch");
  const [editMemo, setEditMemo] = useState("");
  // 태그는 칩으로 관리 — editTagList(확정 칩) + tagDraft(입력 중).
  const [editTagList, setEditTagList] = useState<string[]>([]);
  const [tagDraft, setTagDraft] = useState("");
  // 저널 → 커뮤니티 공유 폼(게시글 생성). show_pnl 켜면 수익률%만 공개(가격/절대손익은 항상 비공개).
  const [sharingId, setSharingId] = useState<number | null>(null);
  const [shareTitle, setShareTitle] = useState("");
  const [shareBody, setShareBody] = useState("");
  const [sharePnl, setSharePnl] = useState(false);
  const [shareBusy, setShareBusy] = useState(false);
  // 카드 액션 오버플로(⋯) 메뉴가 열린 카드.
  const [menuId, setMenuId] = useState<number | null>(null);
  // 복기 대기큐에서 "회고 남기기"로 진입한 카드 + 진입 신호(누를 때마다 증가).
  const [retroAutoId, setRetroAutoId] = useState<number | null>(null);
  const [retroSignal, setRetroSignal] = useState(0);
  // 차트 펼침(작성 시점 대비 등락) 대상 저널.
  const [chartId, setChartId] = useState<number | null>(null);
  // 목록 ↔ 종목 타임라인(한 차트 위 판단 마커) 보기 전환.
  const [view, setView] = useState<"list" | "timeline">("list");
  // 목록 필터/정렬 — 로드된 항목(최대 50건) 기준 클라이언트 사이드.
  const [filterView, setFilterView] = useState<string | null>(null);
  const [filterTag, setFilterTag] = useState<string | null>(null);
  const [sortBy, setSortBy] = useState<"latest" | "change">("latest");
  const subscribed = user?.subscription_active === true;

  useEffect(() => {
    if (subscribed) void load();
  }, [subscribed, load]);

  // ⋯ 메뉴가 열려 있으면 ESC 로 닫는다(포커스 위치와 무관하게 동작).
  useEffect(() => {
    if (menuId === null) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuId(null);
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [menuId]);

  // 저널은 구독 전용 — 비구독자는 구독 유도.
  if (!subscribed)
    return (
      <div className="card px-6 py-8 text-center" data-panel="journal" data-flow="journal-subscribe">
        <p className="font-bold">저널은 구독 회원 전용 기능입니다.</p>
        <p className="mt-2 text-[13.5px] text-muted">
          발행한 리포트에 데이터 방향성 메모를 기록하고, 이후 실제 주가 변동(7·30거래일)을 함께 복기할 수 있습니다.
        </p>
        <Link
          href="/pricing"
          className="brand-grad mt-4 inline-block rounded-full px-6 py-2.5 text-[14px] font-bold text-white"
        >
          구독 안내 보기
        </Link>
      </div>
    );

  if (loading) return <p className="text-muted" data-panel="journal">불러오는 중…</p>;
  if (error) return <p className="text-red" data-panel="journal">{error}</p>;
  if (items.length === 0)
    return <p className="text-muted" data-panel="journal">저장한 저널이 없습니다. 리포트에서 데이터 방향성과 근거를 기록하세요.</p>;

  function startEdit(j: Journal) {
    setEditingId(j.journal_id);
    setEditView(j.user_view);
    setEditMemo(j.memo ?? "");
    setEditTagList(j.tags);
    setTagDraft("");
  }

  // 입력 중인 태그(들)를 칩으로 확정 — 쉼표 붙여넣기도 분리해 받는다(최대 10개, 중복 제거).
  // 하나도 추가되지 않으면(중복·상한) 입력값을 지우지 않아 사용자가 이유를 알 수 있게 둔다.
  function commitTags(raw: string) {
    const parts = raw.split(",").map((t) => t.trim()).filter(Boolean);
    if (parts.length === 0) {
      setTagDraft("");
      return;
    }
    let added = 0;
    setEditTagList((prev) => {
      const next = [...prev];
      for (const p of parts) {
        if (!next.includes(p) && next.length < 10) {
          next.push(p);
          added += 1;
        }
      }
      return next;
    });
    if (added > 0) setTagDraft("");
  }

  function removeTag(tag: string) {
    setEditTagList((prev) => prev.filter((t) => t !== tag));
  }

  async function saveEdit(id: number) {
    // 입력창에 남은 텍스트도 저장 시 칩으로 반영.
    const pending = tagDraft.split(",").map((t) => t.trim()).filter(Boolean);
    const tags = [...new Set([...editTagList, ...pending])].slice(0, 10);
    try {
      await update(id, { user_view: editView, memo: editMemo, tags });
      setEditingId(null);
      showToast("저널을 수정했습니다.", "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  async function removeJournal(id: number) {
    // 삭제는 되돌릴 수 없어 확인을 받는다(즉시 삭제 방지).
    if (!window.confirm("이 저널을 삭제할까요? 삭제하면 되돌릴 수 없습니다.")) return;
    try {
      await remove(id);
      showToast("저널을 삭제했습니다.", "success");
    } catch (e) {
      showToast((e as Error).message, "error");
    }
  }

  // 복기 대기큐 → 해당 카드로 스크롤 + 회고 폼 자동 오픈.
  // 신호를 매번 증가시켜 같은 카드를 다시 눌러도(취소 후 재진입) 폼이 열리게 한다.
  function openRetro(id: number) {
    setRetroAutoId(id);
    setRetroSignal((n) => n + 1);
    if (typeof document !== "undefined") {
      document
        .getElementById(`journal-card-${id}`)
        ?.scrollIntoView({ behavior: "smooth", block: "center" });
    }
  }

  function startShare(j: Journal) {
    setSharingId(j.journal_id);
    setShareTitle(`${j.stock_name ?? j.stock_code} - 데이터 방향성 기록`);
    setShareBody(j.memo ?? "");
    setSharePnl(false);
  }

  async function submitShare(journalId: number) {
    if (!shareTitle.trim()) {
      showToast("제목을 입력하세요.", "error");
      return;
    }
    setShareBusy(true);
    try {
      const post = await createPost({
        journal_id: journalId,
        title: shareTitle.trim(),
        body: shareBody,
        show_pnl: sharePnl,
      });
      setSharingId(null);
      showToast("커뮤니티에 공유했습니다.", "success");
      router.push(`/community/${post.id}`);
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setShareBusy(false);
    }
  }

  const allTags = [...new Set(items.flatMap((j) => j.tags))];
  const stockCodes = [...new Set(items.map((j) => j.stock_code))];
  // 확정 변동(가장 긴 horizon 기준). 미확정은 정렬에서 뒤로.
  const outcomeChange = (j: Journal) =>
    j.outcomes.length > 0 ? j.outcomes[j.outcomes.length - 1].change_pct : null;

  // 데이터 방향성 기록 요약 — 기록 집계일 뿐 성과 평가가 아니다(중립 표현 유지).
  const viewCounts = items.reduce<Record<string, number>>((acc, j) => {
    acc[j.user_view] = (acc[j.user_view] ?? 0) + 1;
    return acc;
  }, {});
  const changes7 = items
    .map((j) => j.outcomes.find((o) => o.horizon === "7td")?.change_pct)
    .filter((v): v is number => v != null);
  const avg7 =
    changes7.length > 0 ? changes7.reduce((a, b) => a + b, 0) / changes7.length : null;
  const pendingRetro = items.filter(isRetroPending);
  const visible = items
    .filter((j) => !filterView || j.user_view === filterView)
    .filter((j) => !filterTag || j.tags.includes(filterTag))
    .slice()
    .sort((a, b) => {
      if (sortBy === "change") {
        return Math.abs(outcomeChange(b) ?? -1) - Math.abs(outcomeChange(a) ?? -1);
      }
      return (b.created_at ?? "").localeCompare(a.created_at ?? "");
    });

  return (
    <div data-panel="journal">
      {/* 데이터 방향성 기록 요약 — 저장한 기록의 중립적 집계 */}
      <div className="card mb-3 flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-3 text-[12.5px]" data-flow="journal-summary">
        <span className="font-bold text-navy">저널 {items.length}건</span>
        {JOURNAL_VIEWS.map(([key, label]) =>
          viewCounts[key] ? (
            <span key={key} className="text-muted">
              {label} <b className="text-navy-soft">{viewCounts[key]}</b>
            </span>
          ) : null,
        )}
        {avg7 != null && (
          <span className="text-muted">
            기록 후 7거래일 평균 변동{" "}
            <b className={avg7 >= 0 ? "text-red" : "text-sky-deep"}>
              {avg7 >= 0 ? "+" : ""}
              {avg7.toFixed(2)}%
            </b>{" "}
            (확정 {changes7.length}건 기준)
          </span>
        )}
        <span className="ml-auto text-[11.5px] text-muted">기록 집계이며 성과 평가가 아닙니다</span>
      </div>

      {/* 복기 대기큐 — 변동이 확정됐는데 회고가 없는 저널. 항목 클릭 시 해당 카드 회고 폼으로 이동 */}
      {view === "list" && pendingRetro.length > 0 && (
        <div className="mb-3 rounded-[12px] bg-surface-2 px-5 py-3 text-[13px]" data-flow="journal-retro-banner">
          <div className="mb-1.5">
            복기할 저널 <b className="text-navy">{pendingRetro.length}건</b> — 변동이 확정됐습니다. 눌러서
            회고를 남겨보세요.
          </div>
          <div className="flex flex-wrap gap-1.5">
            {pendingRetro.map((j) => (
              <button
                key={j.journal_id}
                type="button"
                data-flow="journal-retro-queue-item"
                onClick={() => openRetro(j.journal_id)}
                className="pill flat text-[12px] hover:!border-sky hover:!text-sky-deep"
                style={{ padding: "3px 10px" }}
              >
                {j.stock_name ?? j.stock_code} 회고 남기기 →
              </button>
            ))}
          </div>
        </div>
      )}

      <div className="mb-3 flex flex-wrap items-center gap-2">
        <div className="flex rounded-full bg-surface-2 p-0.5 text-[12.5px] font-semibold">
          <button
            type="button"
            onClick={() => setView("list")}
            className={`rounded-full px-3 py-1 ${view === "list" ? "bg-surface text-navy shadow-sm" : "text-muted"}`}
          >
            목록
          </button>
          <button
            type="button"
            data-flow="journal-timeline"
            onClick={() => setView("timeline")}
            className={`rounded-full px-3 py-1 ${view === "timeline" ? "bg-surface text-navy shadow-sm" : "text-muted"}`}
          >
            종목 타임라인
          </button>
        </div>

        {view === "list" && (
          <>
            <div className="flex gap-1.5" data-flow="journal-filter-view">
              {JOURNAL_VIEWS.map(([key, label]) => (
                <button
                  key={key}
                  type="button"
                  onClick={() => setFilterView(filterView === key ? null : key)}
                  className={`pill flat text-[12px] ${filterView === key ? "!border-sky !text-sky-deep font-bold" : ""}`}
                  style={{ padding: "3px 9px" }}
                >
                  {label}
                </button>
              ))}
            </div>
            {allTags.length > 0 && (
              <select
                value={filterTag ?? ""}
                onChange={(e) => setFilterTag(e.target.value || null)}
                data-flow="journal-filter-tag"
                className="card px-2.5 py-1 text-[12px] outline-none"
              >
                <option value="">태그 전체</option>
                {allTags.map((tag) => (
                  <option key={tag} value={tag}>
                    #{tag}
                  </option>
                ))}
              </select>
            )}
            <select
              value={sortBy}
              onChange={(e) => setSortBy(e.target.value as "latest" | "change")}
              data-flow="journal-sort"
              className="card px-2.5 py-1 text-[12px] outline-none"
            >
              <option value="latest">최신순</option>
              <option value="change">확정 변동 큰 순</option>
            </select>
            {visible.length !== items.length && (
              <span className="text-[12px] text-muted">{visible.length}/{items.length}건</span>
            )}
          </>
        )}
      </div>

      {view === "timeline" ? (
        <div className="space-y-3">
          {stockCodes.map((code) => (
            <div key={code} className="card px-5 py-4">
              <JournalTimelinePanel stockCode={code} />
            </div>
          ))}
        </div>
      ) : (
        <div className="space-y-2">
      {visible.map((j) => {
        const menuOpen = menuId === j.journal_id;
        // 회고 영역은 변동 확정됐거나 이미 회고가 있을 때만 노출(미확정 카드는 깔끔하게).
        const showRetro = j.outcomes.length > 0 || hasRetrospective(j);
        return (
        <div
          key={j.journal_id}
          id={`journal-card-${j.journal_id}`}
          className="card px-5 py-4"
        >
          <div className="flex items-start justify-between gap-2">
            <Link
              href={`/report/${j.stock_code}`}
              className="font-bold hover:text-sky-deep"
            >
              {j.stock_name ?? j.stock_code} <span className="text-[12px] font-normal text-muted">{j.stock_code}</span>
            </Link>
            <div className="flex items-center gap-2">
              <span className="pill flat" style={{ padding: "3px 9px", fontSize: 12 }}>
                {JOURNAL_VIEW_LABEL[j.user_view] ?? j.user_view}
              </span>
              {/* 카드 액션 오버플로 메뉴 — 수정/공유/삭제를 접어 헤더를 정돈 */}
              <div className="relative">
                <button
                  type="button"
                  data-flow="journal-menu"
                  aria-haspopup="menu"
                  aria-expanded={menuOpen}
                  aria-label="저널 액션"
                  onClick={() => setMenuId(menuOpen ? null : j.journal_id)}
                  className="rounded-full px-2 py-0.5 text-[18px] leading-none text-muted hover:bg-surface-2 hover:text-navy"
                >
                  ⋯
                </button>
                {menuOpen && (
                  <>
                    {/* 바깥 클릭으로 닫기(ESC 는 상위 전역 keydown 이 처리) */}
                    <button
                      type="button"
                      aria-hidden
                      tabIndex={-1}
                      onClick={() => setMenuId(null)}
                      className="fixed inset-0 z-10 cursor-default"
                    />
                    <div
                      role="menu"
                      className="card absolute right-0 z-20 mt-1 w-28 overflow-hidden py-1 shadow-md"
                    >
                      <button
                        type="button"
                        role="menuitem"
                        data-flow="journal-edit"
                        onClick={() => {
                          setMenuId(null);
                          if (editingId === j.journal_id) setEditingId(null);
                          else startEdit(j);
                        }}
                        className="block w-full px-4 py-2 text-left text-[13px] font-semibold hover:bg-surface-2"
                      >
                        수정
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        data-flow="journal-share"
                        onClick={() => {
                          setMenuId(null);
                          if (sharingId === j.journal_id) setSharingId(null);
                          else startShare(j);
                        }}
                        className="block w-full px-4 py-2 text-left text-[13px] font-semibold hover:bg-surface-2"
                      >
                        공유
                      </button>
                      <button
                        type="button"
                        role="menuitem"
                        data-flow="journal-delete"
                        onClick={() => {
                          setMenuId(null);
                          void removeJournal(j.journal_id);
                        }}
                        className="block w-full px-4 py-2 text-left text-[13px] font-semibold text-red hover:bg-surface-2"
                      >
                        삭제
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {sharingId === j.journal_id && (
            <div className="mt-3 space-y-2 border-t border-line pt-3" data-flow="journal-share-form">
              <input
                value={shareTitle}
                onChange={(e) => setShareTitle(e.target.value)}
                className="card w-full px-4 py-2.5 text-[13.5px] font-semibold outline-none focus:border-sky"
                placeholder="공유 제목"
              />
              <textarea
                value={shareBody}
                onChange={(e) => setShareBody(e.target.value)}
                rows={2}
                className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky"
                placeholder="공유할 내용 (선택)"
              />
              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={() => setSharePnl((v) => !v)}
                  className={`pill flat text-[12.5px] ${sharePnl ? "!border-sky !text-sky-deep font-bold" : ""}`}
                  style={{ padding: "4px 12px", border: "1px solid var(--color-line)" }}
                >
                  수익률% 공개 {sharePnl ? "ON" : "OFF"}
                </button>
                <span className="text-[11.5px] text-muted">
                  가격·절대손익은 공개되지 않습니다.
                </span>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void submitShare(j.journal_id)}
                  disabled={shareBusy}
                  className="brand-grad rounded-full px-4 py-1.5 text-[13px] font-bold text-white disabled:opacity-50"
                >
                  공유하기
                </button>
                <button type="button" onClick={() => setSharingId(null)} className="text-[13px] text-muted">
                  취소
                </button>
              </div>
            </div>
          )}

          {editingId === j.journal_id ? (
            <div className="mt-3 space-y-2 border-t border-line pt-3">
              <div className="flex flex-wrap gap-2">
                {JOURNAL_VIEWS.map(([key, label]) => (
                  <button
                    key={key}
                    type="button"
                    onClick={() => setEditView(key)}
                    className={`pill flat text-[12.5px] ${editView === key ? "!border-sky !text-sky-deep font-bold" : ""}`}
                    style={{ padding: "4px 10px" }}
                  >
                    {label}
                  </button>
                ))}
              </div>
              <textarea
                value={editMemo}
                onChange={(e) => setEditMemo(e.target.value)}
                rows={2}
                className="card w-full px-4 py-2.5 text-[13.5px] outline-none focus:border-sky"
                placeholder="메모"
              />
              {/* 태그 칩 입력 — Enter/쉼표로 확정, ×로 삭제, 붙여넣기 분리 지원(최대 10개) */}
              <div
                className="card flex flex-wrap items-center gap-1.5 px-3 py-2"
                data-flow="journal-tag-input"
              >
                {editTagList.map((tag) => (
                  <span
                    key={tag}
                    className="pill flat inline-flex items-center gap-1 text-[11.5px]"
                    style={{ padding: "2px 4px 2px 8px" }}
                  >
                    #{tag}
                    <button
                      type="button"
                      aria-label={`태그 ${tag} 삭제`}
                      onClick={() => removeTag(tag)}
                      className="rounded-full px-1 text-muted hover:text-red"
                    >
                      ×
                    </button>
                  </span>
                ))}
                <input
                  value={tagDraft}
                  onChange={(e) => setTagDraft(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === ",") {
                      e.preventDefault();
                      commitTags(tagDraft);
                    } else if (e.key === "Backspace" && !tagDraft && editTagList.length > 0) {
                      removeTag(editTagList[editTagList.length - 1]);
                    }
                  }}
                  onBlur={() => commitTags(tagDraft)}
                  disabled={editTagList.length >= 10}
                  className="min-w-[110px] flex-1 bg-transparent px-1 py-0.5 text-[13px] outline-none disabled:opacity-50"
                  placeholder={editTagList.length >= 10 ? "태그는 최대 10개" : "태그 입력 후 Enter"}
                />
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => void saveEdit(j.journal_id)}
                  className="brand-grad rounded-full px-4 py-1.5 text-[13px] font-bold text-white"
                >
                  저장
                </button>
                <button type="button" onClick={() => setEditingId(null)} className="text-[13px] text-muted">
                  취소
                </button>
              </div>
            </div>
          ) : (
            <>
              {j.memo && <p className="mt-2 text-[13.5px] text-navy-soft">{j.memo}</p>}
              {j.tags.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-1.5">
                  {j.tags.map((tag) => (
                    <span key={tag} className="pill flat text-[11.5px]" style={{ padding: "2px 8px" }}>
                      #{tag}
                    </span>
                  ))}
                </div>
              )}
            </>
          )}

          {/* 회고 — 카드에서 직접(차트 펼침 불필요). 대기큐 진입 시 신호로 편집 오픈 */}
          {showRetro && (
            <div data-flow="journal-card-retro">
              <RetrospectiveBlock
                journal={j}
                autoEditSignal={retroAutoId === j.journal_id ? retroSignal : 0}
              />
            </div>
          )}

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[12px] text-muted">
            <span>{j.created_at?.slice(0, 10)}</span>
            {j.signal_score_at_time != null && (
              <span data-flow="journal-signal-compare">
                그때 <b className="text-navy">{Math.round(j.signal_score_at_time)}</b>
                {j.signal_value_at_time ? ` · ${j.signal_value_at_time}` : ""}
                {/* 현재 발행 신호와 비교 — 신호가 갱신됐으면 "→ 지금"으로 표시 */}
                {j.current_signal?.score != null && (
                  <>
                    {" → 지금 "}
                    <b className="text-navy">{Math.round(j.current_signal.score)}</b>
                    {j.current_signal.value ? ` · ${j.current_signal.value}` : ""}
                  </>
                )}
              </span>
            )}
            {/* 그 후 변동 — 워커가 확정한 7/30거래일 결과. 미확정이면 "확정 전". */}
            {j.outcomes.length > 0 ? (
              j.outcomes.map((o) => (
                <span key={o.horizon}>
                  {HORIZON_LABEL[o.horizon] ?? o.horizon} 후{" "}
                  <b className={o.change_pct >= 0 ? "text-red" : "text-sky-deep"}>
                    {o.change_pct >= 0 ? "+" : ""}
                    {o.change_pct.toFixed(2)}%
                  </b>
                </span>
              ))
            ) : (
              <span>변동 확정 전</span>
            )}
            <button
              type="button"
              data-flow="journal-chart-toggle"
              onClick={() => setChartId(chartId === j.journal_id ? null : j.journal_id)}
              className="ml-auto text-[11.5px] font-semibold hover:text-sky-deep"
            >
              {chartId === j.journal_id ? "차트 닫기 ▲" : "주가 차트 ▼"}
            </button>
          </div>

          {chartId === j.journal_id && <JournalChartPanel journal={j} />}
        </div>
        );
      })}
        </div>
      )}
    </div>
  );
}

function SocialTab() {
  const { links, load, link, unlink } = useSocialStore();
  const showToast = useToastStore((s) => s.show);
  useEffect(() => {
    void load();
  }, [load]);

  async function toggle(provider: (typeof SOCIAL_PROVIDERS)[number]["key"], linked: boolean) {
    try {
      if (linked) {
        await unlink(provider);
        showToast("연동을 해제했습니다.", "success");
        return;
      }
      if (isSocialDevMode(provider)) {
        await link(provider, socialAuthCode(provider));
        showToast("연동했습니다.", "success");
      } else {
        startSocialOAuth(provider, "link"); // provider 로 리다이렉트
      }
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  const linkedMap = new Map(links.map((l) => [l.provider, l.linked]));
  return (
    <div className="max-w-[480px]" data-panel="social">
      <p className="mb-4 text-[13.5px] text-muted">연동하면 다음부터 본인인증 없이 소셜로 간편 로그인할 수 있습니다.</p>
      <div className="space-y-2">
        {SOCIAL_PROVIDERS.map((s) => {
          const linked = linkedMap.get(s.key) ?? false;
          return (
            <div key={s.key} className="card flex items-center justify-between px-5 py-4">
              <span className="font-semibold">{s.label}</span>
              <button
                type="button"
                onClick={() => void toggle(s.key, linked)}
                className={`rounded-full px-4 py-2 text-[13px] font-semibold ${linked ? "border border-line text-navy-soft hover:border-red hover:text-red" : "brand-grad text-white"}`}
              >
                {linked ? "연동 해제" : "연동하기"}
              </button>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function ProfileTab() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const refreshMe = useAuthStore((s) => s.refreshMe);
  const logout = useAuthStore((s) => s.logout);
  const showToast = useToastStore((s) => s.show);
  const [nickname, setNickname] = useState(user?.nickname ?? "");
  const [email, setEmail] = useState(user?.email ?? "");
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!nickname.trim() || !email.trim()) {
      showToast("이메일과 닉네임을 입력해 주세요.", "error");
      return;
    }
    setBusy(true);
    try {
      await updateMe({ nickname: nickname.trim(), email: email.trim() });
      await refreshMe();
      showToast("회원정보를 수정했습니다.", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  async function withdraw() {
    if (!window.confirm("정말 탈퇴하시겠어요? 이 작업은 되돌릴 수 없습니다.")) return;
    try {
      await deleteMe();
      await logout();
      router.push("/");
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  return (
    <div className="card max-w-[480px] p-7" data-panel="profile">
      <dl className="space-y-3 text-[14px]">
        <div className="flex justify-between">
          <dt className="text-muted">회원식별번호</dt>
          <dd className="font-semibold">{user?.member_code}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted">휴대폰</dt>
          <dd className="font-semibold">{user?.phone_masked ?? "—"}</dd>
        </div>
      </dl>
      <label className="mt-5 block text-[13px] font-semibold text-navy-soft">이메일</label>
      <input
        type="email"
        value={email}
        onChange={(e) => setEmail(e.target.value)}
        className="card mt-1 w-full px-4 py-2.5 text-[14px] outline-none focus:border-sky"
      />
      <label className="mt-4 block text-[13px] font-semibold text-navy-soft">닉네임</label>
      <div className="mt-1 flex gap-2">
        <input value={nickname} onChange={(e) => setNickname(e.target.value)} className="card flex-1 px-4 py-2.5 text-[14px] outline-none focus:border-sky" />
        <button type="button" onClick={() => void save()} disabled={busy} data-flow="profile-save" className="brand-grad rounded-full px-5 text-[14px] font-bold text-white disabled:opacity-60">저장</button>
      </div>
      <button type="button" onClick={() => void withdraw()} data-flow="profile-withdraw" className="mt-7 text-[13px] font-semibold text-muted hover:text-red">
        회원 탈퇴
      </button>
    </div>
  );
}
