import Link from 'next/link';

export function Footer() {
  return (
    <footer className="border-t border-neutral-200 bg-white text-neutral-500">
      <div className="mx-auto grid max-w-7xl gap-10 px-4 py-14 md:grid-cols-2 md:px-8">
        <div>
          <span className="text-lg font-black text-neutral-900">
            Signal <span className="text-orange-600">α</span>
          </span>
          <p className="mt-4 text-xs leading-relaxed">
            서울 · Team LENS
            <br />
            본 정보는 투자 권유가 아닙니다.
          </p>
        </div>
        <div className="grid grid-cols-2 gap-6 text-xs sm:grid-cols-4">
          <div>
            <b className="mb-2 block text-neutral-900">서비스</b>
            <Link href="/dashboard/features" className="block py-0.5 hover:text-orange-600">
              소개
            </Link>
            <Link href="/dashboard/agents" className="block py-0.5 hover:text-orange-600">
              에이전트
            </Link>
          </div>
          <div>
            <b className="mb-2 block text-neutral-900">데이터</b>
            <span className="block py-0.5 hover:text-orange-600">DART</span>
            <span className="block py-0.5 hover:text-orange-600">RAG</span>
          </div>
          <div>
            <b className="mb-2 block text-neutral-900">IR</b>
            <span className="block py-0.5 hover:text-orange-600">공시</span>
          </div>
          <div>
            <b className="mb-2 block text-neutral-900">고객센터</b>
            <span className="block py-0.5 hover:text-orange-600">문의</span>
          </div>
        </div>
      </div>
      <div className="border-t border-neutral-100 py-4 text-center text-[10px]">
        © 2026 Signal α · Team LENS
      </div>
    </footer>
  );
}
