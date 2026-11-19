import { PipelineMetrics, PlanCard } from "../types";

export interface AnalyticsDbStats {
  totalBuildersLaunched: number;
  totalBuildersKilled: number;
}

export class AnalyticsEngine {
  compute(
    plans: {
      completed: PlanCard[];
      pending: PlanCard[];
      active: PlanCard[];
      blocked: PlanCard[];
    },
    dbStats: AnalyticsDbStats,
  ): PipelineMetrics {
    const now = Date.now();
    const completed = plans.completed;
    const pending = plans.pending;
    const active = plans.active;
    const blocked = plans.blocked;
    const allPlans = [...completed, ...pending, ...active, ...blocked];

    let totalLifetime = 0;
    let lifetimeCount = 0;
    for (const p of completed) {
      if (p.createdAt) {
        totalLifetime += (now - new Date(p.createdAt).getTime()) / 1000;
        lifetimeCount++;
      }
    }
    const avgLifetime = lifetimeCount > 0 ? totalLifetime / lifetimeCount : 0;

    const sparkline = [0, 0, 0, 0, 0, 0, 0];
    for (let i = 0; i < 7; i++) {
      const d = new Date(now - (6 - i) * 86400000).toISOString().slice(0, 10);
      sparkline[i] = completed.filter(
        (p: any) => p.createdAt?.slice(0, 10) === d,
      ).length;
    }

    const ageBuckets: Record<string, number> = {
      "<1h": 0,
      "1-6h": 0,
      "6-24h": 0,
      "1-7d": 0,
      ">7d": 0,
    };
    for (const p of allPlans) {
      if (!p.createdAt) continue;
      const age = (now - new Date(p.createdAt).getTime()) / 1000;
      if (age < 3600) ageBuckets["<1h"]++;
      else if (age < 21600) ageBuckets["1-6h"]++;
      else if (age < 86400) ageBuckets["6-24h"]++;
      else if (age < 604800) ageBuckets["1-7d"]++;
      else ageBuckets[">7d"]++;
    }

    return {
      totalPlansCompleted: completed.length,
      totalPlansPending: pending.length,
      totalPlansActive: active.length,
      totalPlansBlocked: blocked.length,
      totalBuildersLaunched: dbStats.totalBuildersLaunched,
      totalBuildersKilled: dbStats.totalBuildersKilled,
      averagePlanLifetimeSeconds: Math.round(avgLifetime),
      builderStalenessRate:
        dbStats.totalBuildersLaunched > 0
          ? dbStats.totalBuildersKilled / dbStats.totalBuildersLaunched
          : 0,
      circuitBreakerTrips: 0,
      throughputSparkline: sparkline,
      throughputAvg: sparkline.reduce((a: number, b: number) => a + b, 0) / 7,
      planAgeDistribution: Object.entries(ageBuckets).map(
        ([bucket, count]) => ({ bucket, count }),
      ),
    };
  }
}
