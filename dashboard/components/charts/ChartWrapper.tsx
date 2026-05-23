"use client";

import { lazy, Suspense } from "react";
import type { EChartsOption } from "echarts";

const ReactECharts = lazy(() => import("echarts-for-react"));

export const CHART_COLORS = [
  "#3b82f6",
  "#10b981",
  "#f59e0b",
  "#ef4444",
  "#8b5cf6",
  "#06b6d4",
];

interface ChartWrapperProps {
  option: EChartsOption;
  height?: number;
  loading?: boolean;
  empty?: boolean;
  emptyText?: string;
}

export default function ChartWrapper({
  option,
  height = 300,
  loading = false,
  empty = false,
  emptyText = "No data available",
}: ChartWrapperProps) {
  if (empty) {
    return (
      <div
        className="flex items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-card)] text-sm text-[var(--text-muted)]"
        style={{ height }}
      >
        {emptyText}
      </div>
    );
  }

  const baseOption: EChartsOption = {
    backgroundColor: "transparent",
    textStyle: { color: "#94a3b8" },
    title: { textStyle: { color: "#f1f5f9", fontSize: 14 } },
    legend: {
      textStyle: { color: "#94a3b8" },
      inactiveColor: "#475569",
    },
    color: CHART_COLORS,
    grid: {
      containLabel: true,
      left: 12,
      right: 12,
      top: 40,
      bottom: 12,
    },
    tooltip: {
      backgroundColor: "#111827",
      borderColor: "rgba(255,255,255,0.06)",
      textStyle: { color: "#f1f5f9", fontSize: 12 },
    },
    ...option,
  };

  return (
    <div className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] p-4">
      <Suspense
        fallback={
          <div
            className="flex items-center justify-center text-sm text-[var(--text-muted)]"
            style={{ height }}
          >
            Loading chart…
          </div>
        }
      >
        <ReactECharts
          option={baseOption}
          style={{ height, width: "100%" }}
          opts={{ renderer: "svg" }}
          showLoading={loading}
          loadingOption={{
            text: "Loading…",
            color: "#3b82f6",
            textColor: "#94a3b8",
            maskColor: "rgba(10,15,30,0.8)",
          }}
        />
      </Suspense>
    </div>
  );
}
