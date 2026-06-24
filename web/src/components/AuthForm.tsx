"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useAuthStore } from "@/stores/authStore";
import { isPortoneDevMode } from "@/lib/portone";
import { isSocialDevMode, SOCIAL_PROVIDERS, socialAuthCode, startSocialOAuth } from "@/lib/social";

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
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
      router.push("/mypage");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function onSocial(provider: (typeof SOCIAL_PROVIDERS)[number]["key"]) {
    setError(null);
    if (!isSocialDevMode(provider)) {
      startSocialOAuth(provider, "login"); // provider 로 리다이렉트
      return;
    }
    setBusy(true);
    try {
      await socialLoginWith(provider, socialAuthCode(provider));
      router.push("/mypage");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="mx-auto max-w-[420px] py-16">
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

      {error && <p className="mt-4 text-[13px] text-red">{error}</p>}

      <button
        type="button"
        onClick={() => void onIdentity()}
        disabled={busy}
        className="brand-grad mt-6 w-full rounded-full py-[14px] text-[15px] font-extrabold text-white disabled:opacity-60"
      >
        {busy ? "처리 중…" : isSignup ? "본인인증으로 가입" : "본인인증으로 로그인"}
      </button>
      {isPortoneDevMode() && (
        <p className="mt-2 text-center text-[12px] text-muted">개발 모드: 실제 본인인증 위젯 없이 진행됩니다.</p>
      )}

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
