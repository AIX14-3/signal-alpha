// 구독/결제(플랜·구독·포트원 V2 결제).

import { apiFetch } from "./core";

export type Plan = {
  plan_type: string;
  plan_display_name: string;
  max_watchlist: number;
  signal_delay_hours: number;
  journal_max_entries: number;
  has_alt_data: boolean;
  has_detail_report: boolean;
  has_backtesting: boolean;
  price_monthly: number;
  price_yearly: number;
};

export type Subscription = {
  plan_type: string;
  status: string;
  started_at: string | null;
  expires_at: string | null;
  // 갱신 중지형 해지 예약 시각. 설정돼 있으면 만료일까지만 이용(이후 종료).
  cancelled_at?: string | null;
  billing_cycle: string | null;
  days_remaining?: number | null;
  expiring_soon?: boolean;
};

export type CheckoutInfo = {
  payment_id: string;
  amount: number;
  order_name: string;
  currency: string;
  plan_type: string;
  customer: {
    email: string | null;
    full_name: string | null;
    phone_number: string | null;
  };
};

export type PaymentHistoryItem = {
  payment_id: string;
  status: string;
  amount: number;
  order_name?: string;
  paid_at: string | null;
};

export type Receipt = {
  payment_id: string;
  order_name: string;
  amount: number;
  status: string;
  paid_at: string | null;
  customer: { email: string | null; name: string | null };
};

export async function listPlans(): Promise<{ plans: Plan[] }> {
  return apiFetch("/api/subscriptions/plans", { auth: "none" });
}

export async function getMySubscription(): Promise<{ subscription: Subscription | null; plan: Plan | null }> {
  return apiFetch("/api/subscriptions/me");
}

export async function checkout(cycle: "monthly" | "yearly" = "monthly"): Promise<CheckoutInfo> {
  return apiFetch("/api/payments/checkout", { method: "POST", body: JSON.stringify({ cycle }) });
}

export async function confirmPayment(body: { payment_id: string }): Promise<{
  subscription: Subscription;
}> {
  return apiFetch("/api/payments/confirm", { method: "POST", body: JSON.stringify(body) });
}

export async function cancelPayment(): Promise<{
  status: string;
  expires_at: string | null;
  notice: string;
}> {
  return apiFetch("/api/payments/cancel", { method: "POST" });
}

export async function resumePayment(): Promise<{
  status: string;
  expires_at: string | null;
  notice: string;
}> {
  return apiFetch("/api/payments/resume", { method: "POST" });
}

export async function refundPayment(): Promise<{
  status: string;
  amount: number;
  kind: string;
  notice: string;
}> {
  return apiFetch("/api/payments/refund", { method: "POST" });
}

export async function paymentHistory(): Promise<{ items: PaymentHistoryItem[] }> {
  return apiFetch("/api/payments/history");
}

export async function paymentReceipt(paymentId: string): Promise<Receipt> {
  return apiFetch(`/api/payments/${encodeURIComponent(paymentId)}/receipt`);
}
