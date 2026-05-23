"use client";

import type { EChartsOption } from "echarts";
import ChartWrapper from "./ChartWrapper";
import type { ToolUsageEntry } from "@/lib/types";

interface ToolUsageChartProps {
  data: ToolUsageEntry[];
}

export default function ToolUsageChart({ data }: ToolUsageChartProps) {
  if (!data || data.length === 0) {
    return <ChartWrapper option={{}} empty emptyText="No transcript-derived tool usage" height={300} />;
  }

  /* Sort by count descending, take top 15 */
  const sorted = [...data].sort((a, b) => b.count - a.count).slice(0, 15);
  /* Reverse for horizontal bar (bottom = highest) */
  const reversed = [...sorted].reverse();

  /* Shorten long tool names (e.g. "mcp__pydanticaiDocs__fetch_docs" → "pydanticaiDocs.fetch_docs") */
  const shortenTool = (name: string) => {
    if (name.startsWith("mcp__")) {
      const parts = name.replace("mcp__", "").split("__");
      return parts.length > 1 ? `${parts[0]}.${parts.slice(1).join(".")}` : parts[0];
    }
    return name;
  };
  const names = reversed.map((d) => shortenTool(d.name));
  const fullNames = reversed.map((d) => d.name);
  const counts = reversed.map((d) => d.count);

  const option: EChartsOption = {
    title: {
      text: "Tool Usage",
      left: "center",
      textStyle: { color: "#f1f5f9", fontSize: 14 },
    },
    tooltip: {
      trigger: "axis",
      axisPointer: { type: "shadow" },
      formatter: (params: unknown) => {
        const p = Array.isArray(params) ? params[0] : params;
        const idx = (p as { dataIndex?: number }).dataIndex ?? 0;
        const full = fullNames[idx] || names[idx];
        const val = (p as { value?: number }).value ?? 0;
        return `<b>${full}</b><br/>${val.toLocaleString()} calls`;
      },
    },
    xAxis: {
      type: "value",
      splitLine: { lineStyle: { color: "rgba(255,255,255,0.04)" } },
      axisLabel: { color: "#94a3b8", fontSize: 11 },
    },
    yAxis: {
      type: "category",
      data: names,
      axisLine: { lineStyle: { color: "rgba(255,255,255,0.06)" } },
      axisLabel: {
        color: "#94a3b8",
        fontSize: 11,
        width: 130,
        overflow: "truncate",
      },
    },
    grid: { left: 12, right: 20, top: 40, bottom: 12, containLabel: true },
    series: [
      {
        type: "bar",
        data: counts,
        itemStyle: {
          color: {
            type: "linear",
            x: 0,
            y: 0,
            x2: 1,
            y2: 0,
            colorStops: [
              { offset: 0, color: "rgba(16,185,129,0.4)" },
              { offset: 1, color: "rgba(16,185,129,0.9)" },
            ],
          },
          borderRadius: [0, 4, 4, 0],
        },
        barWidth: "65%",
      },
    ],
  };

  return <ChartWrapper option={option} height={300} />;
}
