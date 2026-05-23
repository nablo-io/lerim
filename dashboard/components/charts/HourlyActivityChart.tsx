"use client";

import type { EChartsOption } from "echarts";
import ChartWrapper from "./ChartWrapper";
import type { HourlyActivityEntry } from "@/lib/types";

interface HourlyActivityChartProps {
  data: HourlyActivityEntry[];
}

export default function HourlyActivityChart({ data }: HourlyActivityChartProps) {
  if (!data || data.length === 0 || data.every((entry) => entry.runs === 0 && entry.tool_calls === 0)) {
    return <ChartWrapper option={{}} empty emptyText="No transcript-derived hourly activity" height={380} />;
  }

  /* Ensure all 24 hours are present, fill gaps with 0 */
  const hourMap = new Map<number, HourlyActivityEntry>();
  data.forEach((d) => hourMap.set(d.hour, d));

  const hours: string[] = [];
  const runs: number[] = [];
  const toolCalls: number[] = [];

  for (let h = 0; h < 24; h++) {
    hours.push(`${h.toString().padStart(2, "0")}:00`);
    const entry = hourMap.get(h);
    runs.push(entry?.runs ?? 0);
    toolCalls.push(entry?.tool_calls ?? 0);
  }

  const option: EChartsOption = {
    title: {
      text: "Operations by Hour",
      left: "center",
      textStyle: { color: "#f1f5f9", fontSize: 14 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
    },
    legend: {
      top: 30,
      textStyle: { color: "#94a3b8" },
    },
    xAxis: {
      type: "category",
      data: hours,
      axisLine: { lineStyle: { color: "rgba(255,255,255,0.06)" } },
      axisLabel: {
        color: "#94a3b8",
        fontSize: 10,
        interval: 2,
      },
    },
    yAxis: [
      {
        type: "value",
        name: "Runs",
        nameTextStyle: { color: "#94a3b8", fontSize: 11 },
        splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
        axisLabel: { color: "#94a3b8", fontSize: 11 },
      },
      {
        type: "value",
        name: "Tool Calls",
        nameTextStyle: { color: "#94a3b8", fontSize: 11 },
        splitLine: { show: false },
        axisLabel: { color: "#94a3b8", fontSize: 11 },
      },
    ],
    grid: { left: 12, right: 20, top: 80, bottom: 40, containLabel: true },
    series: [
      {
        name: "Runs",
        type: "bar",
        data: runs,
        itemStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 0,
            y2: 1,
            colorStops: [
              { offset: 0, color: "rgba(59,130,246,0.8)" },
              { offset: 1, color: "rgba(59,130,246,0.2)" },
            ],
          },
          borderRadius: [3, 3, 0, 0],
        },
        barWidth: "60%",
      },
      {
        name: "Tool Calls",
        type: "line",
        yAxisIndex: 1,
        data: toolCalls,
        smooth: true,
        lineStyle: { color: "#10b981", width: 2 },
        itemStyle: { color: "#10b981" },
        symbol: "circle",
        symbolSize: 4,
      },
    ],
  };

  return <ChartWrapper option={option} height={380} />;
}
