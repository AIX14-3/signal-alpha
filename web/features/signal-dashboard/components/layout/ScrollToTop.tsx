'use client';

import { useEffect, useState } from 'react';
import { ChevronUp } from 'lucide-react';

export function ScrollToTop() {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    const onScroll = () => setVisible(window.scrollY > 400);
    window.addEventListener('scroll', onScroll);
    return () => window.removeEventListener('scroll', onScroll);
  }, []);

  return (
    <button
      type="button"
      onClick={() => window.scrollTo({ top: 0, behavior: 'smooth' })}
      className={`btn-orange fixed bottom-8 right-8 z-50 flex h-12 w-12 items-center justify-center rounded-full shadow-lg transition-all duration-300 ${
        visible ? 'opacity-100' : 'pointer-events-none opacity-0'
      }`}
      title="맨 위로"
    >
      <ChevronUp className="h-5 w-5" />
    </button>
  );
}
