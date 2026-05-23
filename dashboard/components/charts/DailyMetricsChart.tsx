"use client";

import type { EChartsOption } from "echarts";
import ChartWrapper from "./ChartWrapper";
import type { DailyStat } from "@/lib/types";

interface DailyMetricsChartProps {
  daily: DailyStat[];
}

export default function DailyMetricsChart({ daily }: DailyMetricsChartProps) {
  if (daily.length === 0) {
    return <ChartWrapper option={{}} empty emptyText="No transcript-derived daily metrics" height={380} />;
  }

  const dates = daily.map((d) => formatShortDate(d.date));

  const option: EChartsOption = {
    title: {
      text: "Daily Metrics",
      left: "center",
      textStyle: { color: "#f1f5f9", fontSize: 14 },
    },
    tooltip: {
      trigger: "axis",
    },
    legend: {
      top: 30,
      textStyle: { color: "#94a3b8" },
    },
    xAxis: {
      type: "category",
      data: dates,
      axisLine: { lineStyle: { color: "rgba(255,255,255,0.06)" } },
      axisLabel: { color: "#94a3b8", fontSize: 11 },
    },
    yAxis: [
      {
        type: "value",
        name: "Count",
        nameTextStyle: { color: "#94a3b8", fontSize: 11 },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
        axisLabel: { color: "#94a3b8", fontSize: 11 },
      },
      {
        type: "value",
        name: "Tokens",
        nameTextStyle: { color: "#94a3b8", fontSize: 11 },
        splitLine: { show: false },
        axisLabel: {
          color: "#94a3b8",
          fontSize: 11,
          formatter: (value: number) => formatCompact(value),
        },
      },
    ],
    grid: { left: 12, right: 20, top: 80, bottom: 40, containLabel: true },
    series: [
      {
        name: "Messages",
        type: "line",
        smooth: true,
        data: daily.map((d) => d.messages ?? 0),
        lineStyle: { color: "#3b82f6", width: 2 },
        itemStyle: { color: "#3b82f6" },
        symbol: "circle",
        symbolSize: 5,
      },
      {
        name: "Tool Calls",
        type: "line",
        smooth: true,
        data: daily.map((d) => d.tool_calls ?? 0),
        lineStyle: { color: "#10b981", width: 2 },
        itemStyle: { color: "#10b981" },
        symbol: "circle",
        symbolSize: 5,
      },
      {
        name: "Tokens",
        type: "line",
        smooth: true,
        yAxisIndex: 1,
        data: daily.map((d) => d.tokens ?? 0),
        lineStyle: { color: "#f59e0b", width: 2 },
        itemStyle: { color: "#f59e0b" },
        symbol: "circle",
        symbolSize: 5,
      },
    ],
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

function formatCompact(value: number): string {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`;
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}K`;
  return String(value);
}
