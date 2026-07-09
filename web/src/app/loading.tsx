// App Router 라우트 전환 로딩 경계 — Suspense 폴백으로 렌더된다.
export default function Loading() {
  return (
    <div className="relative z-10 mx-auto flex min-h-[50vh] max-w-lg flex-col items-center justify-center px-6 py-16 text-center">
      <div
        className="h-8 w-8 animate-spin rounded-full border-2 border-line border-t-sky-deep"
        aria-hidden="true"
      />
      <p className="mt-4 text-sm text-muted">불러오는 중…</p>
    </div>
  );
}
