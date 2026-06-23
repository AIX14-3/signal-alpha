"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { changeSubscription, getMySubscription, type Plan, type Subscription } from "@/lib/apiClient";
import { won } from "@/lib/format";
import { useAuthStore } from "@/stores/authStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

type Tab = "watchlist" | "subscription" | "profile";

export default function MyPage() {
  const router = useRouter();
  const user = useAuthStore((state) => state.user);
  const status = useAuthStore((state) => state.status);
  const [tab, setTab] = useState<Tab>("watchlist");

  useEffect(() => {
    if (status === "anonymous") router.replace("/login");
  }, [status, router]);

  if (status !== "authenticated" || !user) {
    return <p className="py-16 text-center text-muted">로그인 정보를 확인하는 중…</p>;
  }

  return (
    <div className="py-12">
      <h1 className="text-[32px] font-extrabold">마이페이지</h1>
      <div className="mt-6 flex gap-2 border-b border-line">
        {(
          [
            ["watchlist", "관심종목"],
            ["subscription", "구독"],
            ["profile", "회원정보"],
          ] as [Tab, string][]
        ).map(([key, label]) => (
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
        {tab === "profile" && <ProfileTab email={user.email} nickname={user.nickname} />}
      </div>
    </div>
  );
}

function WatchlistTab() {
  const { items, limit, count, loading, error, load, remove } = useWatchlistStore();

  useEffect(() => {
    void load();
  }, [load]);

  if (loading) return <p className="text-muted">불러오는 중…</p>;
  if (error) return <p className="text-red">{error}</p>;
  if (items.length === 0) {
    return (
      <p className="text-muted">
        관심종목이 없습니다. <Link href="/" className="text-sky-deep">종목 검색하기</Link>
      </p>
    );
  }

  return (
    <div>
      <p className="mb-3 text-[13px] text-muted">
        {count} / {limit}개 등록
      </p>
      <div className="space-y-2">
        {items.map((item) => (
          <div key={item.stock.id} className="card flex items-center justify-between px-5 py-4">
            <Link href={`/report/${item.stock.stock_code}`} className="font-bold hover:text-sky-deep">
              {item.stock.stock_name}
              <span className="ml-2 text-[13px] font-normal text-muted">{item.stock.stock_code}</span>
            </Link>
            <button
              type="button"
              onClick={() => void remove(item.stock.stock_code)}
              className="text-[13px] font-semibold text-muted hover:text-red"
            >
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
  const [message, setMessage] = useState<string | null>(null);

  async function reload() {
    const data = await getMySubscription();
    setSubscription(data.subscription);
    setPlan(data.plan);
  }

  useEffect(() => {
    reload().catch((err) => setMessage((err as Error).message));
  }, []);

  async function cancel() {
    setMessage(null);
    try {
      await changeSubscription({ plan_type: "free", action: "cancel" });
      await reload();
      setMessage("구독을 취소했습니다.");
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <div className="card max-w-[480px] p-7">
      <div className="text-[13px] font-bold uppercase tracking-[0.1em] text-sky-deep">현재 요금제</div>
      <div className="mt-2 text-[28px] font-extrabold">{plan?.plan_display_name ?? "Free"}</div>
      <div className="mt-1 text-[14px] text-muted">
        {subscription
          ? `상태 ${subscription.status} · ${subscription.billing_cycle ?? "-"}`
          : "활성 유료 구독이 없습니다."}
      </div>
      {plan && plan.price_monthly > 0 && (
        <div className="mt-3 text-[15px]">{won(plan.price_monthly)} /월</div>
      )}
      {message && <p className="mt-3 text-[13px] text-sky-deep">{message}</p>}
      <div className="mt-6 flex gap-2">
        <Link href="/pricing" className="brand-grad rounded-full px-5 py-2.5 text-[14px] font-bold text-white">
          요금제 변경
        </Link>
        {subscription && (
          <button
            type="button"
            onClick={() => void cancel()}
            className="rounded-full border border-line px-5 py-2.5 text-[14px] font-semibold text-navy-soft hover:border-red hover:text-red"
          >
            구독 취소
          </button>
        )}
      </div>
    </div>
  );
}

function ProfileTab({ email, nickname }: { email: string; nickname: string | null }) {
  return (
    <div className="card max-w-[480px] p-7">
      <dl className="space-y-3 text-[14px]">
        <div className="flex justify-between">
          <dt className="text-muted">이메일</dt>
          <dd className="font-semibold">{email}</dd>
        </div>
        <div className="flex justify-between">
          <dt className="text-muted">닉네임</dt>
          <dd className="font-semibold">{nickname ?? "-"}</dd>
        </div>
      </dl>
      <p className="mt-5 text-[12.5px] text-muted">
        소셜 로그인 연동(구글·네이버·카카오)은 준비 중입니다.
      </p>
    </div>
  );
}
