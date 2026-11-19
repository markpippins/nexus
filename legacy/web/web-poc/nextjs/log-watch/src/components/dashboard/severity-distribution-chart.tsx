'use client';

import * as React from 'react';
import { Pie, PieChart, Tooltip, Cell } from 'recharts';
import {
  ChartContainer,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart';
import type { Log } from '@/lib/types';
import { useMemo } from 'react';

type SeverityDistributionChartProps = {
  data: Log[];
};

const chartConfig = {
  INFO: { label: 'Info', color: 'hsl(var(--chart-1))' },
  WARNING: { label: 'Warning', color: 'hsl(var(--chart-2))' },
  ERROR: { label: 'Error', color: 'hsl(var(--destructive))' },
  DEBUG: { label: 'Debug', color: 'hsl(var(--chart-3))' },
} satisfies ChartConfig;

const COLORS = {
  INFO: 'hsl(var(--chart-1))',
  WARNING: 'hsl(var(--chart-2))',
  ERROR: 'hsl(var(--destructive))',
  DEBUG: 'hsl(var(--chart-3))',
};

export function SeverityDistributionChart({ data }: SeverityDistributionChartProps) {
  const chartData = useMemo(() => {
    const distribution = data.reduce(
      (acc, log) => {
        acc[log.level] = (acc[log.level] || 0) + 1;
        return acc;
      },
      {} as Record<Log['level'], number>
    );

    return Object.entries(distribution).map(([name, value]) => ({
      name: name as Log['level'],
      value,
      fill: COLORS[name as Log['level']],
    }));
  }, [data]);

  const totalLogs = useMemo(() => chartData.reduce((acc, curr) => acc + curr.value, 0), [chartData]);


  return (
    <ChartContainer
      config={chartConfig}
      className="mx-auto aspect-square h-full max-h-[250px]"
    >
      <PieChart>
        <Tooltip
          cursor={false}
          content={<ChartTooltipContent hideLabel />}
        />
        <Pie
          data={chartData}
          dataKey="value"
          nameKey="name"
          innerRadius={60}
          strokeWidth={5}
        >
          {chartData.map((entry) => (
            <Cell key={`cell-${entry.name}`} fill={entry.fill} />
          ))}
        </Pie>
         <text
            x="50%"
            y="50%"
            textAnchor="middle"
            dominantBaseline="middle"
            className="fill-foreground text-center"
        >
            <tspan
                x="50%"
                dy="-0.5em"
                className="text-3xl font-bold"
            >
                {totalLogs.toLocaleString()}
            </tspan>
            <tspan
                x="50%"
                dy="1.5em"
                className="text-sm text-muted-foreground"
            >
                Total Logs
            </tspan>
        </text>
      </PieChart>
    </ChartContainer>
  );
}
