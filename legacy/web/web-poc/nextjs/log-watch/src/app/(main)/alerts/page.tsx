import { getLogs } from "@/lib/data";
import { flagAnomalousEvents } from "@/ai/flows/flag-anomalous-events";
import { AnomalyList } from "@/components/alerts/anomaly-list";
import { Button } from "@/components/ui/button";

export default async function AlertsPage() {
  const logs = getLogs();
  const { anomalousEvents } = await flagAnomalousEvents({
    logEntries: logs.map(
      (log) => `${log.timestamp} ${log.level} ${log.source}: ${log.message}`
    ),
  });

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-3xl font-bold tracking-tight">Alerts</h1>
          <p className="text-muted-foreground">
            AI-detected anomalies and critical events requiring attention.
          </p>
        </div>
        <Button>Acknowledge All</Button>
      </div>
      <AnomalyList anomalies={anomalousEvents} />
    </div>
  );
}
