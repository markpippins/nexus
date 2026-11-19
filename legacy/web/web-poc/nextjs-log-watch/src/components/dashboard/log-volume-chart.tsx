'use client';

import { Bar, BarChart, XAxis, YAxis, Tooltip } from 'recharts';
import {
  ChartContainer,
  ChartTooltipContent,
  type ChartConfig,
} from '@/components/ui/chart';
import type { Log } from '@/lib/types';
import { useMemo } from 'react';

type LogVolumeChartProps = {
  data: Log[];
};

const chartConfig = {
  logs: {
    label: 'Logs',
    color: 'hsl(var(--primary))',
  },
} satisfies ChartConfig;

export function LogVolumeChart({ data }: LogVolumeChartProps) {
  const chartData = useMemo(() => {
    const hourlyLogs: { [key: string]: { hour: string; logs: number } } = {};
    const now = new Date();

    for (let i = 23; i >= 0; i--) {
      const date = new Date(now);
      date.setHours(now.getHours() - i, 0, 0, 0);
      const hour = date.toLocaleTimeString([], { hour: '2-digit', hour12: false }).replace('24', '00');
      hourlyLogs[hour] = { hour: hour, logs: 0 };
    }
    
    data.forEach(log => {
        const logDate = new Date(log.timestamp);
        if (now.getTime() - logDate.getTime() < 24 * 60 * 60 * 1000) {
            const hour = logDate.toLocaleTimeString([], { hour: '2-digit', hour12: false }).replace('24', '00');
            if (hourlyLogs[hour]) {
                hourlyLogs[hour].logs++;
            }
        }
    });

    return Object.values(hourlyLogs);
  }, [data]);

  return (
    <ChartContainer config={chartConfig} className="h-[250px] w-full">
      <BarChart
        data={chartData}
        margin={{
          top: 5,
          right: 10,
          left: -10,
          bottom: 0,
        }}
      >
        <XAxis
          dataKey="hour"
          tickLine={false}
          axisLine={false}
          tickMargin={8}
          tickFormatter={(value) => value.split(' ')[0]}
        />
        <YAxis
          stroke="#888888"
          fontSize={12}
          tickLine={false}
          axisLine={false}
          tickFormatter={(value) => `${value}`}
          allowDecimals={false}
        />
        <Tooltip
          cursor={false}
          content={<ChartTooltipContent hideLabel />}
          formatter={(value) => `${value} logs`}
        />
        <Bar dataKey="logs" fill="var(--color-logs)" radius={4} />
      </BarChart>
    </ChartContainer>
  );
}
