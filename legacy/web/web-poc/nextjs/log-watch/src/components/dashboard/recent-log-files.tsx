import Link from 'next/link';
import { formatDistanceToNow } from 'date-fns';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import type { LogFile } from '@/lib/types';
import { ArrowUpRight } from 'lucide-react';

type RecentLogFilesProps = {
  files: LogFile[];
};

export function RecentLogFiles({ files }: RecentLogFilesProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center">
        <div className="grid gap-2">
          <CardTitle>Monitored Log Files</CardTitle>
          <CardDescription>
            Log files being actively monitored for changes.
          </CardDescription>
        </div>
        <Button asChild size="sm" className="ml-auto gap-1">
          <Link href="/logs">
            View All
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="grid gap-4">
        {files.map((file) => (
          <div key={file.id} className="grid gap-1">
            <div className="flex items-center gap-2">
              <p className="text-sm font-medium leading-none truncate">{file.name}</p>
              <Badge variant="outline" className="ml-auto">{file.size}</Badge>
            </div>
            <p className="text-sm text-muted-foreground truncate">{file.path}</p>
            <p className="text-xs text-muted-foreground">
              Last modified: {formatDistanceToNow(new Date(file.lastModified), { addSuffix: true })}
            </p>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
