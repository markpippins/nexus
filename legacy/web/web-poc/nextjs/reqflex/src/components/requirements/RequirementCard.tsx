import Link from 'next/link';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import type { Requirement } from '@/lib/types';
import { StatusBadge } from './StatusBadge';
import { Separator } from '@/components/ui/separator';

interface RequirementCardProps {
  requirement: Requirement;
}

export function RequirementCard({ requirement }: RequirementCardProps) {
  return (
    <Link href={`/requirements/${requirement.id}`} className="block group">
      <Card className="h-full flex flex-col transition-all duration-300 ease-in-out group-hover:shadow-lg group-hover:border-primary/50 group-hover:-translate-y-1">
        <CardHeader>
          <div className="flex justify-between items-start gap-4">
            <CardTitle className="text-lg font-semibold leading-tight group-hover:text-primary">{requirement.name}</CardTitle>
            <StatusBadge status={requirement.status} />
          </div>
          <CardDescription className="line-clamp-2 pt-2">{requirement.description}</CardDescription>
        </CardHeader>
        <CardContent className="flex-grow">
          <div className="flex flex-wrap gap-2">
            {requirement.technologies.map((tech) => (
              <Badge key={tech} variant="secondary" className="font-normal">
                {tech}
              </Badge>
            ))}
          </div>
        </CardContent>
        <CardFooter className="text-xs text-muted-foreground justify-between">
           <span>{requirement.subItems.length} sub-items</span>
           <span>Updated: {new Date(requirement.updatedAt).toLocaleDateString()}</span>
        </CardFooter>
      </Card>
    </Link>
  );
}
