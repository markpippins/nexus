import { Badge } from '@/components/ui/badge';
import type { Status } from '@/lib/types';
import { cn } from '@/lib/utils';
import { CheckCircle, Loader, CircleDotDashed } from 'lucide-react';
import * as React from 'react';

interface StatusBadgeProps {
  status: Status;
  className?: string;
}

const statusConfig = {
  pending: {
    label: 'Pending',
    icon: <CircleDotDashed className="h-3 w-3" />,
    className: 'bg-yellow-100 text-yellow-800 dark:bg-yellow-900/70 dark:text-yellow-300 border-yellow-300/50',
  },
  'in-progress': {
    label: 'In Progress',
    icon: <Loader className="h-3 w-3 animate-spin" />,
    className: 'bg-blue-100 text-blue-800 dark:bg-blue-900/70 dark:text-blue-300 border-blue-300/50',
  },
  complete: {
    label: 'Complete',
    icon: <CheckCircle className="h-3 w-3" />,
    className: 'bg-green-100 text-green-800 dark:bg-green-900/70 dark:text-green-300 border-green-300/50',
  },
};

export function StatusBadge({ status, className }: StatusBadgeProps) {
  const config = statusConfig[status];

  if (!config) {
    return null;
  }

  return (
    <Badge
      variant="outline"
      className={cn('flex items-center gap-1.5 capitalize font-medium text-xs py-1 px-2 w-fit', config.className, className)}
    >
      {config.icon}
      <span>{config.label}</span>
    </Badge>
  );
}
