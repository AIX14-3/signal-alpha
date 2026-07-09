"use client";

// 루트 레이아웃까지 무너진 최상위 에러 경계 — 자체 <html>/<body> 를 렌더해야 하고
// globals.css 가 적용되지 않을 수 있어 인라인 스타일로 최소 화면을 보장한다.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <html lang="ko">
      <body
        style={{
          margin: 0,
          minHeight: "100vh",
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          justifyContent: "center",
          gap: "12px",
          padding: "24px",
          textAlign: "center",
          color: "#0f1b33",
          background: "#fbfcfe",
          fontFamily: "Pretendard, -apple-system, system-ui, sans-serif",
        }}
      >
        <h1 style={{ margin: 0, fontSize: "22px", fontWeight: 700 }}>
          예상치 못한 오류가 발생했어요
        </h1>
        <p style={{ margin: 0, color: "#8a97ab" }}>
          페이지를 새로고침하거나 잠시 후 다시 시도해 주세요.
        </p>
        <button
          type="button"
          onClick={reset}
          style={{
            marginTop: "12px",
            border: "none",
            borderRadius: "999px",
            background: "#0f1b33",
            color: "#fff",
            padding: "10px 20px",
            fontSize: "14px",
            fontWeight: 600,
            cursor: "pointer",
          }}
        >
          다시 시도
        </button>
      </body>
    </html>
  );
}
