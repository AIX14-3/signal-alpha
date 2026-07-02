"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { useAuthStore } from "@/stores/authStore";
import { isSocialDevMode, SOCIAL_PROVIDERS, socialAuthCode, startSocialOAuth } from "@/lib/social";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const status = useAuthStore((s) => s.status);
  const loginWithIdentity = useAuthStore((s) => s.loginWithIdentity);
  const signupWithIdentity = useAuthStore((s) => s.signupWithIdentity);
  const socialLoginWith = useAuthStore((s) => s.socialLoginWith);

  const [email, setEmail] = useState("");
  const [nickname, setNickname] = useState("");
  const [agreedRisk, setAgreedRisk] = useState(false);
  const [agreedTerms, setAgreedTerms] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const isSignup = mode === "signup";

  function getReturnTo(): string {
    if (typeof window === "undefined") return "/mypage";
    const to = new URLSearchParams(window.location.search).get("returnTo");
    // 오픈 리다이렉트 방지: 내부 경로만 허용
    return to && to.startsWith("/") ? to : "/mypage";
  }

  // sa_refresh 쿠키로 세션이 이미 복원된(로그인된) 사용자는 로그인/가입 폼을 보여줄
  // 필요가 없다 — 부팅 hydrate 확정 즉시 목적지로 돌려보낸다.
  useEffect(() => {
    if (status === "authenticated") router.replace(getReturnTo());
  }, [status, router]);

  async function onIdentity() {
    setError(null);
    if (isSignup) {
      if (!email.trim() || !nickname.trim()) {
        setError("이메일과 닉네임을 입력해 주세요.");
        return;
      }
      if (!agreedRisk || !agreedTerms) {
        setError("필수 약관에 동의해야 가입할 수 있습니다.");
        return;
      }
    }
    setBusy(true);
    try {
      if (isSignup) {
        await signupWithIdentity({ email: email.trim(), nickname: nickname.trim() });
      } else {
        await loginWithIdentity();
      }
      router.push(getReturnTo());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onSocial(provider: (typeof SOCIAL_PROVIDERS)[number]["key"]) {
    setError(null);
    if (!isSocialDevMode(provider)) {
      startSocialOAuth(provider, "login", getReturnTo()); // provider 로 리다이렉트
      return;
    }
    setBusy(true);
    try {
      await socialLoginWith(provider, socialAuthCode(provider));
      router.push(getReturnTo());
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  // 로그인 상태(또는 hydrate 진행 중)에는 폼을 그리지 않는다 — 이동 중 플래시 방지.
  if (status === "authenticated") {
    return (
      <p className="py-16 text-center text-muted" data-page={isSignup ? "signup" : "login"}>
        이미 로그인되어 있습니다. 이동 중…
      </p>
    );
  }

  return (
    <div className="mx-auto max-w-[420px] py-16" data-page={isSignup ? "signup" : "login"}>
      <h1 className="text-[28px] font-extrabold">{isSignup ? "회원가입" : "로그인"}</h1>
      <p className="mt-1 text-[14px] text-muted">
        {isSignup
          ? "포트원 본인인증으로 가입합니다. 휴대폰 번호로 1회만 가입할 수 있습니다."
          : "아이디·비밀번호 없이 본인인증으로 로그인합니다."}
      </p>

      {isSignup && (
        <div className="mt-7 space-y-3">
          <input
            type="email"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            placeholder="이메일 (결제·안내에 사용)"
            className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky"
          />
          <input
            value={nickname}
            onChange={(e) => setNickname(e.target.value)}
            placeholder="닉네임"
            className="card w-full px-4 py-3 text-[15px] outline-none focus:border-sky"
          />
          <label className="flex items-start gap-2 px-1 text-[13px] text-navy-soft">
            <input type="checkbox" checked={agreedRisk} onChange={(e) => setAgreedRisk(e.target.checked)} />
            <span>[필수] Signal α는 투자 권유가 아닌 데이터 방향성·근거 제공 서비스임에 동의합니다.</span>
          </label>
          <label className="flex items-start gap-2 px-1 text-[13px] text-navy-soft">
            <input type="checkbox" checked={agreedTerms} onChange={(e) => setAgreedTerms(e.target.checked)} />
            <span>[필수] 서비스 이용약관·개인정보 처리방침에 동의합니다.</span>
          </label>
        </div>
      )}

      {error && <p className="mt-4 text-[13px] text-red" role="alert">{error}</p>}

      <button
        type="button"
        onClick={() => void onIdentity()}
        disabled={busy}
        data-flow={isSignup ? "identity-signup" : "identity-login"}
        className="brand-grad mt-6 w-full rounded-full py-[14px] text-[15px] font-extrabold text-white disabled:opacity-60"
      >
        {busy ? "처리 중…" : isSignup ? "본인인증으로 가입" : "본인인증으로 로그인"}
      </button>

      {!isSignup && (
        <>
          <div className="my-6 flex items-center gap-3 text-[12px] text-muted">
            <span className="h-px flex-1 bg-line" /> 연동된 소셜로 간편 로그인 <span className="h-px flex-1 bg-line" />
          </div>
          <div className="grid grid-cols-3 gap-2">
            {SOCIAL_PROVIDERS.map((s) => (
              <button
                key={s.key}
                type="button"
                onClick={() => void onSocial(s.key)}
                disabled={busy}
                data-flow="social-login"
                data-provider={s.key}
                className="rounded-full border border-line py-3 text-[14px] font-semibold text-navy-soft hover:border-navy hover:text-navy disabled:opacity-60"
              >
                {s.label}
              </button>
            ))}
          </div>
          <p className="mt-2 text-center text-[12px] text-muted">
            소셜 연동은 가입 후 마이페이지에서 진행합니다.
          </p>
        </>
      )}

      <p className="mt-6 text-center text-[14px] text-muted">
        {isSignup ? (
          <>
            이미 계정이 있나요? <Link href="/login" className="font-semibold text-sky-deep">로그인</Link>
          </>
        ) : (
          <>
            계정이 없나요? <Link href="/signup" className="font-semibold text-sky-deep">회원가입</Link>
          </>
        )}
      </p>
    </div>
  );
}
