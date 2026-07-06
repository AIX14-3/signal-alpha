// 관리자 화면 공용 날짜 포맷(KST 로케일 표기).

export function fmtDateTime(value: string | null): string {
  if (!value) return '-';
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? value : d.toLocaleString('ko-KR', { timeZone: 'Asia/Seoul' });
}
