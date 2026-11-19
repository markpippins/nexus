import Link from 'next/link';
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import type { AnomalousEvent } from '@/lib/types';
import { AlertCircle, ArrowUpRight } from 'lucide-react';
import { Badge } from '@/components/ui/badge';

type RecentAnomaliesProps = {
  anomalies: AnomalousEvent[];
};

export function RecentAnomalies({ anomalies }: RecentAnomaliesProps) {
  return (
    <Card>
      <CardHeader className="flex flex-row items-center">
        <div className="grid gap-2">
          <CardTitle>Recent Anomalies</CardTitle>
          <CardDescription>
            AI-flagged events from the last hour.
          </CardDescription>
        </div>
        <Button asChild size="sm" className="ml-auto gap-1">
          <Link href="/alerts">
            View All
            <ArrowUpRight className="h-4 w-4" />
          </Link>
        </Button>
      </CardHeader>
      <CardContent className="grid gap-4">
        {anomalies.length === 0 ? (
          <div className="flex flex-col items-center justify-center text-center p-8">
            <AlertCircle className="size-10 text-muted-foreground mb-4" />
            <p className="font-semibold">No anomalies detected</p>
            <p className="text-sm text-muted-foreground">
              The system is operating normally.
            </p>
          </div>
        ) : (
          anomalies.slice(0, 4).map((anomaly, index) => (
            <div key={index} className="grid gap-1">
                <p className="text-sm font-medium leading-none truncate">
                    {anomaly.reason}
                </p>
                <div className="flex items-center gap-2">
                    <p className="text-sm text-muted-foreground truncate">{anomaly.logEntry.split(': ')[1] || anomaly.logEntry}</p>
                    <Badge variant="outline" className="ml-auto whitespace-nowrap">
                       {anomaly.logEntry.includes("ERROR") ? "Error" : anomaly.logEntry.includes("WARNING") ? "Warning" : "Info"}
                    </Badge>
                </div>
            </div>
          ))
        )}
      </CardContent>
    </Card>
  );
}
