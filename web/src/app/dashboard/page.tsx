import { HomeContent } from '@/features/signal-dashboard/components/home/HomeContent';

interface HomePageProps {
  searchParams: Promise<{ focus?: string }>;
}

export default async function HomePage({ searchParams }: HomePageProps) {
  const params = await searchParams;
  const focusSearch = params.focus === 'search';

  return <HomeContent focusSearch={focusSearch} />;
}
