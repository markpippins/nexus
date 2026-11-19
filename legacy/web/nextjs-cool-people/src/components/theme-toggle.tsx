'use client';

import * as React from 'react';
import { Moon, Sun } from 'lucide-react';
import { useTheme } from '@/components/theme-provider';
import { Button } from '@/components/ui/button';

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const [mounted, setMounted] = React.useState(false);

  React.useEffect(() => {
    setMounted(true);
  }, []);

  if (!mounted) {
    return (
      <Button variant="ghost" className="w-full justify-start" disabled>
        <Sun className="mr-2 h-[1.2rem] w-[1.2rem] rotate-0 scale-100 transition-all" />
        Toggle Theme
      </Button>
    );
  }

  return (
    <Button variant="ghost" className="w-full justify-start" onClick={toggleTheme}>
      {theme === 'light' ? (
        <>
          <Sun className="mr-2 h-[1.2rem] w-[1.2rem] rotate-0 scale-100 transition-all" />
          <span>Light Mode</span>
        </>
      ) : (
        <>
          <Moon className="mr-2 h-[1.2rem] w-[1.2rem] rotate-90 scale-0 transition-all dark:rotate-0 dark:scale-100" />
          <span>Dark Mode</span>
        </>
      )}
      <span className="sr-only">Toggle theme</span>
    </Button>
  );
}
