"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { checkout, confirmPayment, listPlans, type Plan } from "@/lib/apiClient";
import { won } from "@/lib/format";
import { pay } from "@/lib/portone";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";

export default function PricingPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const refreshMe = useAuthStore((s) => s.refreshMe);
  const showToast = useToastStore((s) => s.show);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    listPlans()
      .then((data) => setPlans(data.plans))
      .catch((err) => showToast((err as Error).message, "error"));
  }, []); // eslint-disable-line react-hooks/exhaustive-deps

  async function subscribe() {
    if (!user) {
      router.push("/login");
      return;
    }
    setBusy(true);
    try {
      const info = await checkout();
      const payment_id = await pay({
        paymentId: info.payment_id,
        orderName: info.order_name,
        amount: info.amount,
        customerEmail: user?.email ?? undefined,
        customerName: user?.nickname ?? undefined,
      });
      await confirmPayment({ payment_id });
      await refreshMe();
      showToast("구독이 시작되었습니다.", "success");
      router.push("/mypage");
    } catch (err) {
      showToast((err as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  const free = plans.find((p) => p.plan_type === "free");
  const paid = plans.find((p) => p.plan_type !== "free");
  const price = paid?.price_monthly ?? 9900;

  return (
    <div className="py-12">
      <h1 className="text-[32px] font-extrabold">요금 안내</h1>
      <p className="mt-1 text-[14px] text-muted">필요한 만큼 선택하세요. 언제든 취소할 수 있고, 남은 무료 열람은 보존됩니다.</p>

      <div className="mt-8 grid grid-cols-1 gap-5 md:grid-cols-2">
        <div className="card flex flex-col p-7">
          <div className="text-[13px] font-bold uppercase tracking-[0.1em] text-muted">무료</div>
          <div className="mt-3 text-[32px] font-extrabold">0원</div>
          <ul className="mt-5 flex-1 space-y-2 text-[14px] text-navy-soft">
            <li>· 리포트 무료 3회 열람</li>
            <li>· 관심종목 무제한</li>
            <li>· 비회원은 DART·네이버만 공개</li>
            <li>· 저널 {free?.journal_max_entries ?? 50}건</li>
          </ul>
          <button type="button" onClick={() => router.push(user ? "/" : "/signup")} className="mt-6 rounded-full border border-line py-3 text-[15px] font-bold text-navy-soft hover:border-navy hover:text-navy">
            {user ? "종목 검색하기" : "무료로 시작"}
          </button>
        </div>

        <div className="card flex flex-col border-2 border-sky p-7">
          <div className="text-[13px] font-bold uppercase tracking-[0.1em] text-sky-deep">월 구독 · 추천</div>
          <div className="mt-3 text-[32px] font-extrabold">
            {won(price)}<span className="text-[14px] font-medium text-muted"> /월</span>
          </div>
          <ul className="mt-5 flex-1 space-y-2 text-[14px] text-navy-soft">
            <li>· 리포트 <strong>무제한 열람</strong></li>
            <li>· 5개 소스 상세 전체</li>
            <li>· 관심종목 무제한 · 저널 무제한</li>
            <li>· 언제든 취소(무료 잔여분 보존)</li>
          </ul>
          <button type="button" onClick={() => void subscribe()} disabled={busy} className="brand-grad mt-6 rounded-full py-3 text-[15px] font-bold text-white disabled:opacity-60">
            {busy ? "처리 중…" : "구독하기"}
          </button>
        </div>
      </div>

      <p className="mt-6 rounded-[12px] bg-surface-2 p-4 text-[12.5px] text-muted">
        결제는 포트원 KG이니시스 일반결제로 진행됩니다. Signal α는 투자 권유가 아닌 데이터 방향성·근거 제공 서비스입니다.
      </p>
    </div>
  );
}
