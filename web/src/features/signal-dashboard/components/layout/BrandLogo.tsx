interface BrandLogoProps {
  variant?: 'hero' | 'header';
}

export function BrandLogo({ variant = 'header' }: BrandLogoProps) {
  if (variant === 'hero') {
    return (
      <div className="mb-8 flex items-center gap-4">
        <div className="brand-mark flex h-14 w-14 shrink-0 items-center justify-center rounded-xl shadow-lg shadow-orange-500/30">
          <span className="text-2xl font-black leading-none text-white">α</span>
        </div>
        <div>
          <p className="text-2xl font-black tracking-tight text-white md:text-3xl">
            Signal <span className="text-orange-500">α</span>
          </p>
          <p className="mt-1 text-xs font-bold uppercase tracking-[0.2em] text-orange-400">
            Investment Intelligence
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-2.5">
      <div className="brand-mark flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
        <span className="text-lg font-black leading-none text-white">α</span>
      </div>
      <span className="text-lg font-black tracking-tight text-neutral-900">
        Signal <span className="text-orange-600">α</span>
      </span>
    </div>
  );
}
