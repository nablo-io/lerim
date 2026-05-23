"use client";

import type { EChartsOption } from "echarts";
import ChartWrapper, { CHART_COLORS } from "./ChartWrapper";

interface AgentDistributionChartProps {
  byAgent: Record<string, { runs: number }>;
}

export default function AgentDistributionChart({ byAgent }: AgentDistributionChartProps) {
  const entries = Object.entries(byAgent);

  if (entries.length === 0) {
    return <ChartWrapper option={{}} empty emptyText="No transcript-derived agent data" height={380} />;
  }

  const data = entries.map(([name, stats], i) => ({
    name,
    value: stats.runs,
    itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
  }));

  const option: EChartsOption = {
    title: {
      text: "Agent Distribution",
      left: "center",
      textStyle: { color: "#f1f5f9", fontSize: 14 },
    },
    tooltip: {
      trigger: "item",
      formatter: "{b}: {c} runs ({d}%)",
    },
    legend: {
      orient: "vertical",
      left: "left",
      top: "middle",
      textStyle: { color: "#94a3b8" },
    },
    series: [
      {
        type: "pie",
        radius: ["40%", "70%"],
        center: ["60%", "55%"],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 6,
          borderColor: "#0a0f1e",
          borderWidth: 2,
        },
        label: {
          show: false,
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 13,
            fontWeight: "bold",
            color: "#f1f5f9",
          },
        },
        labelLine: { show: false },
        data,
      },
    ],
  };

  return <ChartWrapper option={option} height={380} />;
}
