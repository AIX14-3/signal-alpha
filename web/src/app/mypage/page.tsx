"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import {
  cancelPayment,
  checkout,
  confirmPayment,
  deleteMe,
  getMySubscription,
  listJournals,
  updateMe,
  type Journal,
  type Plan,
  type Subscription,
} from "@/lib/apiClient";
import { won } from "@/lib/format";
import { pay } from "@/lib/portone";
import { SOCIAL_PROVIDERS, socialAuthCode } from "@/lib/social";
import { useAuthStore } from "@/stores/authStore";
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

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated" || !user) {
    return <p className="py-16 text-center text-muted">로그인 정보를 확인하는 중…</p>;
  }

  return (
    <div className="py-12">
      <h1 className="text-[32px] font-extrabold">
        마이페이지 <span className="pill flat align-middle text-[13px]" style={{ padding: "3px 9px" }}>{user.member_code}</span>
      </h1>
      <div className="mt-6 flex flex-wrap gap-2 border-b border-line">
        {TABS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => setTab(key)}
            className={`-mb-px border-b-2 px-4 py-3 text-[14.5px] font-semibold ${
              tab === key ? "border-sky text-navy" : "border-transparent text-muted"
            }`}
          >
            {label}
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

  if (loading) return <p className="text-muted">불러오는 중…</p>;
  if (error) return <p className="text-red">{error}</p>;
  if (items.length === 0)
    return (
      <p className="text-muted">
        관심종목이 없습니다. <Link href="/" className="text-sky-deep">종목 검색하기</Link>
      </p>
    );

  return (
    <div>
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
  const [busy, setBusy] = useState(false);
  const showToast = useToastStore((s) => s.show);
  const refreshMe = useAuthStore((s) => s.refreshMe);

  async function reload() {
    const data = await getMySubscription();
    setSubscription(data.subscription);
    setPlan(data.plan);
  }
  useEffect(() => {
    reload().catch((err) => showToast((err as Error).message, "error"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function subscribe() {
    setBusy(true);
    try {
      const info = await checkout();
      const payment_id = await pay({ paymentId: info.payment_id, orderName: info.order_name, amount: info.amount });
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
    setBusy(true);
    try {
      await cancelPayment();
      await reload();
      await refreshMe();
      showToast("구독을 취소했습니다. 남은 무료 열람은 그대로 사용할 수 있어요.", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  const active = subscription?.status === "active";

  return (
    <div className="card max-w-[480px] p-7">
      <div className="text-[13px] font-bold uppercase tracking-[0.1em] text-sky-deep">현재 상태</div>
      <div className="mt-2 text-[28px] font-extrabold">{active ? "월 구독 중" : "무료"}</div>
      <div className="mt-1 text-[14px] text-muted">
        {active && subscription?.expires_at
          ? `만료 ${subscription.expires_at.slice(0, 10)}`
          : "무료 회원은 리포트를 3회까지 열람할 수 있습니다."}
      </div>
      <div className="mt-3 text-[15px]">{won(plan?.price_monthly && plan.price_monthly > 0 ? plan.price_monthly : 9900)} /월 · 무제한 열람</div>
      <div className="mt-6 flex gap-2">
        {active ? (
          <button type="button" onClick={() => void cancel()} disabled={busy} className="rounded-full border border-line px-5 py-2.5 text-[14px] font-semibold text-navy-soft hover:border-red hover:text-red disabled:opacity-60">
            구독 취소
          </button>
        ) : (
          <button type="button" onClick={() => void subscribe()} disabled={busy} className="brand-grad rounded-full px-6 py-2.5 text-[14px] font-bold text-white disabled:opacity-60">
            {busy ? "처리 중…" : "월 9,900원 구독하기"}
          </button>
        )}
        <Link href="/pricing" className="rounded-full border border-line px-5 py-2.5 text-[14px] font-semibold text-navy-soft hover:text-navy">
          요금 안내
        </Link>
      </div>
    </div>
  );
}

function JournalTab() {
  const [items, setItems] = useState<Journal[]>([]);
  const [loading, setLoading] = useState(true);
  useEffect(() => {
    listJournals({ limit: 50 })
      .then((d) => setItems(d.items))
      .finally(() => setLoading(false));
  }, []);

  if (loading) return <p className="text-muted">불러오는 중…</p>;
  if (items.length === 0) return <p className="text-muted">저장한 저널이 없습니다. 리포트에서 저장해 투자 추이를 기록하세요.</p>;

  const VIEW: Record<string, string> = { watch: "계속 관찰", research_more: "추가 확인 필요", not_relevant: "낮은 관련도" };
  return (
    <div className="space-y-2">
      {items.map((j) => (
        <div key={j.journal_id} className="card px-5 py-4">
          <div className="flex items-center justify-between">
            <Link href={`/report/${j.stock_code}`} className="font-bold hover:text-sky-deep">
              {j.stock_name ?? j.stock_code} <span className="text-[12px] font-normal text-muted">{j.stock_code}</span>
            </Link>
            <span className="pill flat" style={{ padding: "3px 9px", fontSize: 12 }}>{VIEW[j.user_view] ?? j.user_view}</span>
          </div>
          {j.memo && <p className="mt-2 text-[13.5px] text-navy-soft">{j.memo}</p>}
          <div className="mt-1 text-[12px] text-muted">{j.created_at?.slice(0, 10)}</div>
        </div>
      ))}
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
      if (linked) await unlink(provider);
      else await link(provider, socialAuthCode(provider));
      showToast(linked ? "연동을 해제했습니다." : "연동했습니다.", "success");
    } catch (err) {
      showToast((err as Error).message, "error");
    }
  }

  const linkedMap = new Map(links.map((l) => [l.provider, l.linked]));
  return (
    <div className="max-w-[480px]">
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
  const [busy, setBusy] = useState(false);

  async function save() {
    setBusy(true);
    try {
      await updateMe({ nickname });
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
    <div className="card max-w-[480px] p-7">
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
      <label className="mt-5 block text-[13px] font-semibold text-navy-soft">닉네임</label>
      <div className="mt-1 flex gap-2">
        <input value={nickname} onChange={(e) => setNickname(e.target.value)} className="card flex-1 px-4 py-2.5 text-[14px] outline-none focus:border-sky" />
        <button type="button" onClick={() => void save()} disabled={busy} className="brand-grad rounded-full px-5 text-[14px] font-bold text-white disabled:opacity-60">저장</button>
      </div>
      <button type="button" onClick={() => void withdraw()} className="mt-7 text-[13px] font-semibold text-muted hover:text-red">
        회원 탈퇴
      </button>
    </div>
  );
}
