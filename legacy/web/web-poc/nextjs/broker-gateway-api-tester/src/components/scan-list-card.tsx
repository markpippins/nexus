'use client';

import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import ScanTable from '@/components/scan-table';
import type { Scan } from '@/types';
import { Skeleton } from '@/components/ui/skeleton';

interface ScanListCardProps {
  scans: Scan[];
  isProcessingAny: boolean; // To show loading state on the table
}

export default function ScanListCard({ scans, isProcessingAny }: ScanListCardProps) {
  return (
    <Card className="shadow-lg">
      <CardHeader>
        <CardTitle>Scanned Documents</CardTitle>
        <CardDescription>View the status of your uploaded and processed documents.</CardDescription>
      </CardHeader>
      <CardContent>
        {isProcessingAny && scans.length === 0 ? (
          <div className="space-y-2">
            <Skeleton className="h-12 w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-10 w-full" />
          </div>
        ) : scans.length === 0 ? (
          <p className="text-center text-muted-foreground py-8">No documents scanned yet. Upload a file to get started.</p>
        ) : (
          <ScanTable scans={scans} />
        )}
      </CardContent>
    </Card>
  );
}