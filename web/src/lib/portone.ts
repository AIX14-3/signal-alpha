// 포트원 V2 브라우저 SDK 래퍼.
// 항상 real 모드로 동작한다(@portone/browser-sdk v2). storeId + channelKey(본인인증=KG이니시스
// 통합인증, 결제=이니시스 일반결제)를 env 로 주입한다. 미설정 시 SDK 호출이 실패한다.

import * as PortOne from "@portone/browser-sdk/v2";

const STORE_ID = process.env.NEXT_PUBLIC_PORTONE_STORE_ID;
const CHANNEL_IDENTITY = process.env.NEXT_PUBLIC_PORTONE_CHANNEL_KEY_IDENTITY;
const CHANNEL_PAYMENT = process.env.NEXT_PUBLIC_PORTONE_CHANNEL_KEY_PAYMENT;

function randomId(prefix: string): string {
  const rand =
    typeof crypto !== "undefined" && crypto.randomUUID
      ? crypto.randomUUID()
      : `${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
  return `${prefix}-${rand}`;
}

/** 본인인증 → identityVerificationId. */
export async function certify(): Promise<string> {
  const identityVerificationId = randomId("identity");
  const res = await PortOne.requestIdentityVerification({
    storeId: STORE_ID as string,
    identityVerificationId,
    channelKey: CHANNEL_IDENTITY as string,
  });
  if (!res || res.code) throw new Error(res?.message ?? "본인인증에 실패했습니다.");
  return res.identityVerificationId;
}

/** 결제 → paymentId. 백엔드 checkout 이 발급한 paymentId 로 결제창을 띄운다.
 *  이니시스는 고객 이메일을 요구하므로 customer.email 을 함께 전달한다. */
export async function pay(opts: {
  paymentId: string;
  orderName: string;
  amount: number;
  customerEmail?: string;
  customerName?: string;
  customerPhone?: string;
}): Promise<string> {
  const res = await PortOne.requestPayment({
    storeId: STORE_ID as string,
    channelKey: CHANNEL_PAYMENT as string,
    paymentId: opts.paymentId,
    orderName: opts.orderName,
    totalAmount: opts.amount,
    currency: "KRW",
    payMethod: "CARD",
    customer: {
      email: opts.customerEmail,
      fullName: opts.customerName,
      phoneNumber: opts.customerPhone,
    },
  });
  if (!res || res.code) throw new Error(res?.message ?? "결제에 실패했습니다.");
  return res.paymentId;
}
