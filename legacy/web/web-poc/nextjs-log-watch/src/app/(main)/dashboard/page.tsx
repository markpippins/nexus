import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getLogs, getLogFiles } from "@/lib/data";
import { flagAnomalousEvents } from "@/ai/flows/flag-anomalous-events";
import { LogVolumeChart } from "@/components/dashboard/log-volume-chart";
import { SeverityDistributionChart } from "@/components/dashboard/severity-distribution-chart";
import { RecentAnomalies } from "@/components/dashboard/recent-anomalies";
import { RecentLogFiles } from "@/components/dashboard/recent-log-files";
import { OverviewCard } from "@/components/dashboard/overview-card";
import { Signal, AlertTriangle, CircleX, Info } from "lucide-react";

export default async function DashboardPage() {
  const allLogs = getLogs();
  const logFiles = getLogFiles();
  const recentLogs = getLogs(50);

  const anomalousEventsPromise = flagAnomalousEvents({
    logEntries: recentLogs.map(
      (log) => `${log.timestamp} ${log.level} ${log.source}: ${log.message}`
    ),
  });

  const [anomalousEventsResult] = await Promise.all([anomalousEventsPromise]);

  const overviewStats = {
    totalLogs: allLogs.length,
    warnings: allLogs.filter((log) => log.level === "WARNING").length,
    errors: allLogs.filter((log) => log.level === "ERROR").length,
    anomalies: anomalousEventsResult.anomalousEvents.length,
  };

  return (
    <>
      <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-4">
        <OverviewCard
          title="Total Logs (24h)"
          value={overviewStats.totalLogs.toLocaleString()}
          icon={Signal}
          description="Total log entries processed"
        />
        <OverviewCard
          title="Warnings"
          value={overviewStats.warnings.toLocaleString()}
          icon={AlertTriangle}
          description="Potential issues detected"
          color="text-yellow-500"
        />
        <OverviewCard
          title="Errors"
          value={overviewStats.errors.toLocaleString()}
          icon={CircleX}
          description="Critical errors needing attention"
          color="text-red-500"
        />
        <OverviewCard
          title="Anomalies"
          value={overviewStats.anomalies.toLocaleString()}
          icon={Info}
          description="AI-flagged unusual events"
          color="text-accent"
        />
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-7">
        <Card className="lg:col-span-4">
          <CardHeader>
            <CardTitle>Log Volume</CardTitle>
            <CardDescription>
              Number of log entries over the last 24 hours.
            </CardDescription>
          </CardHeader>
          <CardContent className="pl-2">
            <LogVolumeChart data={allLogs} />
          </CardContent>
        </Card>
        <Card className="lg:col-span-3">
          <CardHeader>
            <CardTitle>Severity Distribution</CardTitle>
            <CardDescription>
              Distribution of log levels across all entries.
            </CardDescription>
          </CardHeader>
          <CardContent>
            <SeverityDistributionChart data={allLogs} />
          </CardContent>
        </Card>
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <RecentAnomalies
          anomalies={anomalousEventsResult.anomalousEvents}
        />
        <RecentLogFiles files={logFiles} />
      </div>
    </>
  );
}
