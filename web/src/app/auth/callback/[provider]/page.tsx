"use client";

import { useParams, useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { linkSocial, socialLogin, type Provider } from "@/lib/apiClient";
import { setUserTokens } from "@/lib/session";
import { callbackUri, clearOAuthState, readOAuthState } from "@/lib/social";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";

export default function SocialCallbackPage() {
  const params = useParams<{ provider: string }>();
  const provider = params.provider as Provider;
  const router = useRouter();
  const hydrate = useAuthStore((s) => s.hydrate);
  const showToast = useToastStore((s) => s.show);
  const [message, setMessage] = useState("소셜 인증을 처리하는 중…");
  const ran = useRef(false);

  useEffect(() => {
    if (ran.current) return;
    ran.current = true;

    const sp = new URLSearchParams(window.location.search);
    const code = sp.get("code");
    const returnedState = sp.get("state");
    const oauthError = sp.get("error");
    const saved = readOAuthState();
    clearOAuthState();

    const fallback = saved?.intent === "login" ? "/login" : "/mypage";
    const fail = (msg: string) => {
      setMessage(msg);
      showToast(msg, "error");
      setTimeout(() => router.replace(fallback), 1500);
    };

    if (oauthError || !code) return fail("인증이 취소되었거나 실패했습니다.");
    if (!saved || saved.provider !== provider) return fail("잘못된 콜백 요청입니다.");
    if (returnedState && saved.state !== returnedState) return fail("보안 검증(state)에 실패했습니다.");

    const body = {
      code,
      redirect_uri: callbackUri(provider),
      state: returnedState ?? undefined,
    };

    if (saved.intent === "link") {
      linkSocial(provider, body)
        .then(() => {
          showToast(`${provider} 연동을 완료했습니다.`, "success");
          router.replace("/mypage");
        })
        .catch((err) => fail((err as Error).message));
    } else {
      socialLogin(provider, body)
        .then(async (res) => {
          setUserTokens(res.access_token); // refresh 는 HttpOnly 쿠키로 설정됨
          await hydrate();
          showToast("로그인되었습니다.", "success");
          router.replace(saved.returnTo || "/mypage");
        })
        .catch((err) => fail((err as Error).message));
    }
  }, [provider, router, hydrate, showToast]);

  return <div className="py-24 text-center text-muted" data-page="social-callback">{message}</div>;
}
