import { Layers3 } from 'lucide-react';
import Link from 'next/link';
import { cn } from '@/lib/utils';
import { Button } from '@/components/ui/button';

export function Header() {
  return (
    <header className="sticky top-0 z-50 w-full border-b bg-card/80 backdrop-blur">
      <div className="container mx-auto flex h-16 items-center">
        <Link href="/" className="flex items-center gap-2" prefetch={false}>
          <Layers3 className="h-7 w-7 text-primary" />
          <span className={cn('text-xl font-bold tracking-tight text-foreground font-headline')}>
            ReqFlex
          </span>
        </Link>
      </div>
    </header>
  );
}
