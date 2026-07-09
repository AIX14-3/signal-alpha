import Link from "next/link";

// App Router 404 경계 — 존재하지 않는 라우트(잘못된 종목 코드 등)에서 렌더된다.
export default function NotFound() {
  return (
    <div className="relative z-10 mx-auto flex min-h-[60vh] max-w-lg flex-col items-center justify-center px-6 py-16 text-center">
      <p className="text-sm font-semibold tracking-[0.3em] text-sky-deep">404</p>
      <h1 className="mt-3 text-2xl font-bold text-navy">페이지를 찾을 수 없어요</h1>
      <p className="mt-3 text-muted">
        요청하신 주소가 바뀌었거나 존재하지 않습니다. 종목 코드나 링크를 다시 확인해 주세요.
      </p>
      <Link
        href="/"
        className="mt-8 inline-flex items-center rounded-full bg-navy px-5 py-2.5 text-sm font-semibold text-white transition hover:opacity-90"
      >
        홈으로 돌아가기
      </Link>
    </div>
  );
}
