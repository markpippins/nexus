'use client';

import * as React from 'react';
import type { AnomalousEvent } from '@/lib/types';
import {
  Card,
  CardContent,
  CardDescription,
  CardFooter,
  CardHeader,
  CardTitle,
} from '@/components/ui/card';
import { Button } from '@/components/ui/button';
import { Check, AlertCircle } from 'lucide-react';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/hooks/use-toast';
import { cn } from '@/lib/utils';

type AnomalyListProps = {
  anomalies: AnomalousEvent[];
};

export function AnomalyList({ anomalies }: AnomalyListProps) {
  const [acknowledged, setAcknowledged] = React.useState<Set<number>>(new Set());
  const { toast } = useToast();

  const handleAcknowledge = (index: number) => {
    setAcknowledged((prev) => {
      const newSet = new Set(prev);
      newSet.add(index);
      return newSet;
    });
    toast({
      title: 'Alert Acknowledged',
      description: 'The anomaly has been marked as reviewed.',
    });
  };

  if (anomalies.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center text-center p-12 border-2 border-dashed rounded-lg">
        <AlertCircle className="size-12 text-muted-foreground mb-4" />
        <h2 className="text-xl font-semibold">No Anomalies Detected</h2>
        <p className="text-muted-foreground">
          The system is currently operating within normal parameters.
        </p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-1 gap-4">
      {anomalies.map((anomaly, index) => (
        <Card key={index} className={cn(acknowledged.has(index) && 'bg-muted/50')}>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>{anomaly.reason}</span>
              <Badge variant={anomaly.logEntry.includes("ERROR") ? "destructive" : "secondary"}>
                {anomaly.logEntry.includes("ERROR") ? "High Severity" : "Medium Severity"}
              </Badge>
            </CardTitle>
            <CardDescription>
              AI has flagged this event as unusual.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <pre className="bg-muted p-4 rounded-md text-xs font-mono overflow-x-auto">
              <code>{anomaly.logEntry}</code>
            </pre>
          </CardContent>
          <CardFooter>
            <Button
              size="sm"
              variant="outline"
              onClick={() => handleAcknowledge(index)}
              disabled={acknowledged.has(index)}
              className="ml-auto"
            >
              <Check className="mr-2 h-4 w-4" />
              {acknowledged.has(index) ? 'Acknowledged' : 'Acknowledge'}
            </Button>
          </CardFooter>
        </Card>
      ))}
    </div>
  );
}
