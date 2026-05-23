"use client";

import type { EChartsOption } from "echarts";
import ChartWrapper, { CHART_COLORS } from "./ChartWrapper";

interface ModelUsageChartProps {
  data: Record<string, { tokens: number; sessions: number }>;
}

function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}K`;
  return n.toLocaleString();
}

function shortenModelName(name: string): string {
  // "claude-opus-4-6" -> "opus-4-6"
  // "claude-haiku-4-5-20251001" -> "haiku-4-5"
  // "claude-sonnet-4-6" -> "sonnet-4-6"
  let short = name.replace(/^claude-/, "");
  // Remove long date suffixes like "-20251001"
  short = short.replace(/-\d{8,}$/, "");
  return short;
}

export default function ModelUsageChart({ data }: ModelUsageChartProps) {
  const entries = Object.entries(data || {});

  if (entries.length === 0) {
    return <ChartWrapper option={{}} empty emptyText="No transcript-derived model data" height={300} />;
  }

  /* Sort by tokens descending, take top 8 */
  const sorted = entries
    .sort((a, b) => b[1].tokens - a[1].tokens)
    .slice(0, 8);

  const pieData = sorted.map(([name, stats], i) => ({
    name: shortenModelName(name),
    value: stats.tokens,
    sessions: stats.sessions,
    fullName: name,
    itemStyle: { color: CHART_COLORS[i % CHART_COLORS.length] },
  }));

  const option: EChartsOption = {
    title: {
      text: "Model Usage",
      left: "center",
      textStyle: { color: "#f1f5f9", fontSize: 14 },
    },
    tooltip: {
      trigger: "item",
      formatter: (params: unknown) => {
        const p = params as { name: string; value: number; percent: number; data: { sessions: number; fullName: string } };
        return `${p.data.fullName}<br/>Tokens: ${formatTokens(p.value)}<br/>Sessions: ${p.data.sessions}<br/>${p.percent}%`;
      },
    },
    legend: {
      orient: "vertical",
      left: 10,
      top: "middle",
      textStyle: { color: "#94a3b8", fontSize: 11 },
      formatter: (name: string) => {
        const item = pieData.find((d) => d.name === name);
        return item ? `${name}  ${formatTokens(item.value)}` : name;
      },
    },
    series: [
      {
        type: "pie",
        radius: ["35%", "60%"],
        center: ["65%", "55%"],
        avoidLabelOverlap: true,
        itemStyle: {
          borderRadius: 6,
          borderColor: "#0a0f1e",
          borderWidth: 2,
        },
        label: {
          show: true,
          position: "outside",
          formatter: "{d}%",
          color: "#94a3b8",
          fontSize: 11,
        },
        emphasis: {
          label: {
            show: true,
            fontSize: 13,
            fontWeight: "bold",
            color: "#f1f5f9",
          },
        },
        labelLine: {
          show: true,
          length: 10,
          length2: 8,
          lineStyle: { color: "rgba(148,163,184,0.3)" },
        },
        data: pieData,
      },
    ],
  };

  return <ChartWrapper option={option} height={300} />;
}
