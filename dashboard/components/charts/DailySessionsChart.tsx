"use client";

import type { EChartsOption } from "echarts";
import ChartWrapper, { CHART_COLORS } from "./ChartWrapper";
import type { DailyStat } from "@/lib/types";

interface DailySessionsChartProps {
  daily: DailyStat[];
  byAgent: Record<string, { runs: number }>;
}

export default function DailySessionsChart({ daily, byAgent }: DailySessionsChartProps) {
  if (daily.length === 0) {
    return <ChartWrapper option={{}} empty emptyText="No transcript-derived daily session data" height={380} />;
  }

  const dates = daily.map((d) => formatShortDate(d.date));
  const agentNames = Object.keys(byAgent);

  /* If we have per-agent data, show single total line; per-agent daily breakdown
     would require the API to return daily data per agent. For now, show total runs. */
  const series: EChartsOption["series"] = agentNames.length > 0
    ? [
        {
          name: "Total Sessions",
          type: "line",
          smooth: true,
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(59,130,246,0.3)" },
                { offset: 1, color: "rgba(59,130,246,0.02)" },
              ],
            },
          },
          lineStyle: { color: "#3b82f6", width: 2 },
          itemStyle: { color: "#3b82f6" },
          symbol: "circle",
          symbolSize: 6,
          data: daily.map((d) => d.runs),
        },
      ]
    : [
        {
          name: "Sessions",
          type: "line",
          smooth: true,
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(59,130,246,0.3)" },
                { offset: 1, color: "rgba(59,130,246,0.02)" },
              ],
            },
          },
          lineStyle: { color: "#3b82f6", width: 2 },
          itemStyle: { color: "#3b82f6" },
          symbol: "circle",
          symbolSize: 6,
          data: daily.map((d) => d.runs),
        },
      ];

  const option: EChartsOption = {
    title: {
      text: "Daily Sessions",
      left: "center",
      textStyle: { color: "#f1f5f9", fontSize: 14 },
    },
    tooltip: {
      trigger: "axis",
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: "rgba(255,255,255,0.06)" } },
      axisLabel: { color: "#94a3b8", fontSize: 11 },
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
      axisLabel: { color: "#94a3b8", fontSize: 11 },
    },
    legend: { top: 30, textStyle: { color: "#94a3b8", fontSize: 11 } },
    grid: { left: 12, right: 20, top: 80, bottom: 40, containLabel: true },
    color: CHART_COLORS,
    series,
  };

  return <ChartWrapper option={option} height={380} />;
}

function formatShortDate(iso: string): string {
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch {
    return iso;
  }
}
