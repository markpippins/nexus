'use client';

import { ChevronRight, Home } from 'lucide-react';

interface BreadcrumbsProps {
  path: string[];
  onNavigate: (path: string[]) => void;
}

export function Breadcrumbs({ path, onNavigate }: BreadcrumbsProps) {
  return (
    <nav aria-label="Breadcrumb">
      <ol className="flex items-center space-x-1 text-sm text-muted-foreground">
        <li>
          <button onClick={() => onNavigate([])} className="flex items-center gap-1.5 hover:text-primary transition-colors">
            <Home className="h-4 w-4" />
            <span className="sr-only">Home</span>
          </button>
        </li>
        {path.map((segment, index) => (
          <li key={index}>
            <div className="flex items-center space-x-1">
              <ChevronRight className="h-4 w-4" />
              <button
                onClick={() => onNavigate(path.slice(0, index + 1))}
                className={`hover:text-primary transition-colors ${index === path.length - 1 ? 'font-medium text-foreground' : ''}`}
                aria-current={index === path.length - 1 ? 'page' : undefined}
              >
                {segment}
              </button>
            </div>
          </li>
        ))}
      </ol>
    </nav>
  );
}
