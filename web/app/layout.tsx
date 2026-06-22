import type { Metadata } from 'next';
import { Inter, Noto_Sans_KR } from 'next/font/google';
import './globals.css';

const inter = Inter({
  subsets: ['latin'],
  variable: '--font-inter',
  display: 'swap',
});

const notoSansKr = Noto_Sans_KR({
  subsets: ['latin'],
  weight: ['300', '400', '500', '700', '900'],
  variable: '--font-noto-sans-kr',
  display: 'swap',
});

export const metadata: Metadata = {
  title: 'Signal α - Investment Intelligence',
  description: '공시 · 리포트 · 대체 데이터를 AI가 교차 검증하는 투자 인텔리전스 대시보드',
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ko">
      <body className={`${inter.variable} ${notoSansKr.variable} antialiased`}>
        {children}
      </body>
    </html>
  );
}
