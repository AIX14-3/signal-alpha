"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { changeSubscription, listPlans, type Plan } from "@/lib/apiClient";
import { won } from "@/lib/format";
import { useAuthStore } from "@/stores/authStore";

function features(plan: Plan): string[] {
  return [
    `관심종목 ${plan.max_watchlist}개`,
    plan.signal_delay_hours === 0 ? "실시간 시그널" : `시그널 ${plan.signal_delay_hours}시간 지연`,
    `저널 ${plan.journal_max_entries}건`,
    plan.has_alt_data ? "대체데이터 포함" : "대체데이터 미포함",
    plan.has_detail_report ? "상세 리포트" : "기본 리포트",
    plan.has_backtesting ? "백테스팅 지원" : "백테스팅 미지원",
  ];
}

export default function PricingPage() {
  const router = useRouter();
  const user = useAuthStore((state) => state.user);
  const [plans, setPlans] = useState<Plan[]>([]);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    listPlans()
      .then((data) => setPlans(data.plans))
      .catch((err) => setMessage((err as Error).message));
  }, []);

  async function subscribe(planType: string) {
    if (!user) {
      router.push("/login");
      return;
    }
    setMessage(null);
    try {
      await changeSubscription({ plan_type: planType, action: "subscribe", billing_cycle: "monthly" });
      setMessage(`${planType} 구독으로 변경했습니다.`);
      router.push("/mypage");
    } catch (err) {
      setMessage((err as Error).message);
    }
  }

  return (
    <div className="py-12">
      <h1 className="text-[32px] font-extrabold">요금제</h1>
      <p className="mt-1 text-[14px] text-muted">필요한 만큼 선택하세요. 언제든 변경·취소할 수 있습니다.</p>
      {message && <p className="mt-3 text-[14px] text-sky-deep">{message}</p>}

      <div className="mt-8 grid grid-cols-1 gap-5 lg:grid-cols-3">
        {plans.map((plan) => (
          <div key={plan.plan_type} className="card flex flex-col p-7">
            <div className="text-[13px] font-bold uppercase tracking-[0.1em] text-sky-deep">
              {plan.plan_display_name}
            </div>
            <div className="mt-3 text-[32px] font-extrabold">
              {plan.price_monthly === 0 ? "무료" : won(plan.price_monthly)}
              {plan.price_monthly > 0 && <span className="text-[14px] font-medium text-muted"> /월</span>}
            </div>
            <ul className="mt-5 flex-1 space-y-2 text-[14px] text-navy-soft">
              {features(plan).map((feature) => (
                <li key={feature}>· {feature}</li>
              ))}
            </ul>
            <button
              type="button"
              onClick={() => void subscribe(plan.plan_type)}
              className="brand-grad mt-6 rounded-full py-3 text-[15px] font-bold text-white"
            >
              {plan.plan_type === "free" ? "무료로 시작" : "구독하기"}
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
