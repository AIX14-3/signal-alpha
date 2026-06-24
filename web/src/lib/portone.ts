// 포트원(아임포트) 브라우저 SDK 래퍼.
// - dev 모드(NEXT_PUBLIC_PORTONE_IMP_CODE 미설정): 실제 위젯 없이 결정적 imp_uid 를 만든다.
//   본인인증 imp_uid 는 기기별로 고정(localStorage)해 signup→login 이 같은 phone 으로 매칭된다.
//   (백엔드도 dev 모드에서 imp_uid→phone 을 결정적으로 도출하므로 흐름이 일치한다.)
// - real 모드: IMP SDK 를 로드해 certification / request_pay 를 호출한다.

const IMP_CODE = process.env.NEXT_PUBLIC_PORTONE_IMP_CODE;
const DEV_IMP_KEY = "sa_dev_impuid";

export function isPortoneDevMode(): boolean {
  return !IMP_CODE;
}

type IMPInstance = {
  init: (code: string) => void;
  certification: (
    data: Record<string, unknown>,
    cb: (rsp: { success: boolean; imp_uid?: string; error_msg?: string }) => void,
  ) => void;
  request_pay: (
    data: Record<string, unknown>,
    cb: (rsp: {
      success: boolean;
      imp_uid?: string;
      merchant_uid?: string;
      error_msg?: string;
    }) => void,
  ) => void;
};

declare global {
  interface Window {
    IMP?: IMPInstance;
  }
}

function devImpUid(): string {
  if (typeof window === "undefined") return "imp_dev_ssr";
  let id = window.localStorage.getItem(DEV_IMP_KEY);
  if (!id) {
    id = `imp_dev_${Math.random().toString(36).slice(2)}${Date.now().toString(36)}`;
    window.localStorage.setItem(DEV_IMP_KEY, id);
  }
  return id;
}

async function loadSdk(): Promise<IMPInstance> {
  if (window.IMP) return window.IMP;
  await new Promise<void>((resolve, reject) => {
    const script = document.createElement("script");
    script.src = "https://cdn.iamport.kr/v1/iamport.js";
    script.onload = () => resolve();
    script.onerror = () => reject(new Error("포트원 SDK 로드 실패"));
    document.head.appendChild(script);
  });
  const imp = window.IMP as IMPInstance | undefined;
  if (!imp) throw new Error("포트원 SDK 초기화 실패");
  imp.init(IMP_CODE as string);
  return imp;
}

/** 본인인증 → imp_uid. dev 모드는 기기 고정 imp_uid 반환. */
export async function certify(): Promise<string> {
  if (isPortoneDevMode()) return devImpUid();
  const imp = await loadSdk();
  return new Promise((resolve, reject) => {
    imp.certification({ merchant_uid: `cert_${Date.now()}` }, (rsp) => {
      if (rsp.success && rsp.imp_uid) resolve(rsp.imp_uid);
      else reject(new Error(rsp.error_msg ?? "본인인증에 실패했습니다."));
    });
  });
}

/** 일반결제 → imp_uid. dev 모드는 합성 imp_uid 반환(백엔드 dev 검증이 금액=상품가/paid 로 처리). */
export async function pay(opts: {
  merchant_uid: string;
  amount: number;
  name: string;
  pg: string;
}): Promise<string> {
  if (isPortoneDevMode()) return `imp_dev_pay_${Math.random().toString(36).slice(2)}`;
  const imp = await loadSdk();
  return new Promise((resolve, reject) => {
    imp.request_pay(
      { pg: opts.pg, pay_method: "card", merchant_uid: opts.merchant_uid, name: opts.name, amount: opts.amount },
      (rsp) => {
        if (rsp.success && rsp.imp_uid) resolve(rsp.imp_uid);
        else reject(new Error(rsp.error_msg ?? "결제에 실패했습니다."));
      },
    );
  });
}
