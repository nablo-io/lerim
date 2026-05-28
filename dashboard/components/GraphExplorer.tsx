"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import {
  CanvasEvent,
  ComboEvent,
  EdgeEvent,
  Graph,
  NodeEvent,
  type ComboData,
  type EdgeData,
  type GraphData,
  type GraphOptions,
  type IElementEvent,
  type NodeData,
} from "@antv/g6";
import { api } from "@/lib/api";
import { formatRecordKind, formatScopeLabel, humanizeToken } from "@/lib/labels";
import type { GraphEdge, GraphNode } from "@/lib/types";

interface GraphExplorerProps {
  onRecordClick?: (recordId: string) => void;
}

type ClusterBy = "semantic" | "off";
type Selection =
  | { kind: "node"; node: GraphNode }
  | { kind: "edge"; edge: GraphEdge }
  | { kind: "cluster"; cluster: ClusterSummary }
  | null;

type GraphState = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  totalRecords: number;
  truncated: boolean;
};

type ClusterSummary = {
  id: string;
  label: string;
  lensLabel: string;
  description: string;
  representativeTitles: string[];
  topKinds: string[];
  topProjects: string[];
  color: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

type NodeDatumData = {
  record: GraphNode;
  color: string;
  label: string;
  size: number;
};

type EdgeDatumData = {
  edge: GraphEdge;
  color: string;
  label: string;
  width: number;
};

type ComboDatumData = {
  cluster: ClusterSummary;
  color: string;
  label: string;
  radius: number;
};

const DEFAULT_MAX_NODES = 100;
const DEFAULT_EDGE_LENGTH = 230;

const KIND_COLORS: Record<string, string> = {
  decision: "#60a5fa",
  constraint: "#f87171",
  preference: "#2dd4bf",
  fact: "#a78bfa",
  reference: "#fbbf24",
  episode: "#94a3b8",
  context_record: "#64748b",
};

const RELATION_COLORS: Record<GraphEdge["kind"], string> = {
  supports: "#4ade80",
  refines: "#60a5fa",
  depends_on: "#c084fc",
  contradicts: "#fb7185",
  same_topic: "#94a3b8",
  evidence_for: "#2dd4bf",
  supersedes: "#fbbf24",
  related: "#cbd5e1",
};

const RELATION_DESCRIPTIONS: Record<GraphEdge["kind"], string> = {
  supports: "Strengthens or confirms another record.",
  refines: "Makes another record more specific.",
  depends_on: "Relies on another record being true.",
  contradicts: "Conflicts with another record.",
  same_topic: "Shares a reusable topic.",
  evidence_for: "Provides concrete evidence.",
  supersedes: "Replaces weaker or older context.",
  related: "Useful adjacency without a stronger relation.",
};

const CLUSTER_COLORS = [
  "#60a5fa",
  "#2dd4bf",
  "#a78bfa",
  "#fb7185",
  "#fbbf24",
  "#22d3ee",
  "#818cf8",
  "#f472b6",
  "#4ade80",
  "#c084fc",
];

const CLUSTER_OPTIONS: Array<{ value: ClusterBy; label: string; hint: string }> = [
  { value: "semantic", label: "Topic", hint: "Semantic topics" },
  { value: "off", label: "Off", hint: "Show records without cluster bubbles" },
];

function edgeKey(edge: GraphEdge, index: number) {
  return edge.id || `${edge.source}:${edge.kind}:${edge.target}:${index}`;
}

function relationshipLabel(edge: GraphEdge) {
  return humanizeToken(edge.kind);
}

function relationshipDetailLabel(edge: GraphEdge) {
  const label = edge.label?.trim();
  if (!label || label.toLowerCase() === relationshipLabel(edge).toLowerCase()) return "";
  return label;
}

function shortLabel(value?: string | null, max = 34) {
  const label = value?.trim() || "Untitled";
  return label.length > max ? `${label.slice(0, max - 1)}...` : label;
}

function nodeColor(node: GraphNode) {
  return KIND_COLORS[(node.record_kind || "").toLowerCase()] || "#64748b";
}

function edgeColor(edge: GraphEdge) {
  return RELATION_COLORS[edge.kind] || "#64748b";
}

function alphaColor(hex: string, alpha: number) {
  const normalized = hex.replace("#", "");
  const padded = normalized.length === 3
    ? normalized.split("").map((value) => `${value}${value}`).join("")
    : normalized;
  const value = Number.parseInt(padded, 16);
  if (Number.isNaN(value)) return `rgba(96, 165, 250, ${alpha})`;
  const red = (value >> 16) & 255;
  const green = (value >> 8) & 255;
  const blue = value & 255;
  return `rgba(${red}, ${green}, ${blue}, ${alpha})`;
}

function compactDate(value?: string | null) {
  if (!value) return "No date";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "No date";
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function truncate(value?: string | null, max = 520) {
  const text = value?.trim();
  if (!text) return "";
  return text.length > max ? `${text.slice(0, max).trim()}...` : text;
}

function rankedValues(values: Array<string | null | undefined>, limit: number) {
  const counts = new Map<string, number>();
  values.forEach((value) => {
    const normalized = value?.trim();
    if (!normalized) return;
    counts.set(normalized, (counts.get(normalized) || 0) + 1);
  });

  return Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, limit)
    .map(([value]) => value);
}

function representativeTitles(nodes: GraphNode[]) {
  return nodes
    .map((node) => node.label?.trim() || node.summary?.trim() || node.body?.trim())
    .filter((value): value is string => Boolean(value))
    .slice(0, 4)
    .map((value) => shortLabel(value, 58));
}

function clusterValue(node: GraphNode) {
  return node.semantic_cluster || "semantic_unclustered";
}

function clusterLabel(value: string) {
  if (value === "unclustered" || value === "semantic_unclustered") return "Unclustered";
  return humanizeToken(value.replace(/^semantic_/, "Topic "));
}

function clusterId(value: string) {
  return `cluster:${encodeURIComponent(value)}`;
}

function clusterDescription(nodes: GraphNode[]) {
  const titles = representativeTitles(nodes);
  const kinds = rankedValues(nodes.map((node) => node.record_kind), 2).map(formatRecordKind);
  const projects = rankedValues(nodes.map((node) => node.project), 2).map(formatScopeLabel);

  const parts = [];
  if (kinds.length) parts.push(kinds.join(" / "));
  if (projects.length) parts.push(projects.join(" + "));
  if (titles.length) parts.push(titles.slice(0, 2).join(" - "));
  return parts.join(" - ") || "No summary available";
}

function clusterTitle(cluster: ClusterSummary) {
  const scores = new Map(cluster.nodes.map((node, index) => [
    node.id,
    {
      index,
      score: Math.max(0, node.confidence || 0),
      updatedAt: node.updated_at || "",
    },
  ]));
  cluster.edges.forEach((edge) => {
    const source = scores.get(edge.source);
    const target = scores.get(edge.target);
    if (!source || !target) return;
    const weight = Math.max(0.2, edge.weight || 0.45);
    source.score += weight;
    target.score += weight;
  });
  const representative = [...cluster.nodes].sort((a, b) => {
    const aScore = scores.get(a.id);
    const bScore = scores.get(b.id);
    const scoreDelta = (bScore?.score || 0) - (aScore?.score || 0);
    if (scoreDelta !== 0) return scoreDelta;
    const dateDelta = (bScore?.updatedAt || "").localeCompare(aScore?.updatedAt || "");
    if (dateDelta !== 0) return dateDelta;
    return (aScore?.index || 0) - (bScore?.index || 0);
  })[0];
  return shortLabel(
    representative?.label || representative?.summary || representative?.body || cluster.label,
    38,
  );
}

function buildClusters(nodes: GraphNode[], edges: GraphEdge[], clusterBy: ClusterBy) {
  const clusters = new Map<string, ClusterSummary>();
  const nodeCluster = new Map<string, string>();
  const nodeIds = new Set(nodes.map((node) => node.id));

  if (clusterBy === "off") return { clusters: [], nodeCluster };

  nodes.forEach((node) => {
    const key = clusterValue(node);
    const id = clusterId(key);
    const existing = clusters.get(id);
    const cluster = existing || {
      id,
      label: clusterLabel(key),
      lensLabel: clusterLabel(key),
      description: "",
      representativeTitles: [],
      topKinds: [],
      topProjects: [],
      color: CLUSTER_COLORS[clusters.size % CLUSTER_COLORS.length],
      nodes: [],
      edges: [],
    };

    cluster.nodes.push(node);
    clusters.set(id, cluster);
    nodeCluster.set(node.id, id);
  });

  edges.forEach((edge) => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
    const sourceCluster = nodeCluster.get(edge.source);
    const targetCluster = nodeCluster.get(edge.target);
    if (sourceCluster) clusters.get(sourceCluster)?.edges.push(edge);
    if (targetCluster && targetCluster !== sourceCluster) clusters.get(targetCluster)?.edges.push(edge);
  });

  const summarizedClusters = Array.from(clusters.values()).map((cluster) => {
    const lensLabel = cluster.label;
    const summarized = {
      ...cluster,
      lensLabel,
      description: clusterDescription(cluster.nodes),
      representativeTitles: representativeTitles(cluster.nodes),
      topKinds: rankedValues(cluster.nodes.map((node) => node.record_kind), 3).map(formatRecordKind),
      topProjects: rankedValues(cluster.nodes.map((node) => node.project), 3).map(formatScopeLabel),
    };
    return {
      ...summarized,
      label: clusterTitle(summarized),
    };
  });

  return { clusters: summarizedClusters, nodeCluster };
}

function nodeSize(node: GraphNode, prominent = false) {
  const base = prominent ? 34 : 30;
  const min = prominent ? 32 : 28;
  const max = prominent ? 54 : 48;
  return Math.max(min, Math.min(max, base + (node.confidence || 0.5) * 16));
}

function edgeWidth(edge: GraphEdge) {
  return Math.max(1.2, Math.min(3.6, 1.2 + (edge.weight || 0.45) * 2.4));
}

function clusterRadius(cluster: ClusterSummary, edgeLength: number) {
  const count = cluster.nodes.length;
  return Math.max(88, Math.min(210, 64 + Math.sqrt(count) * 23 + Math.min(count, 34) * 1.35 + edgeLength * 0.04));
}

function clusterPlacements(clusters: ClusterSummary[], edgeLength: number) {
  const ranked = [...clusters].sort((a, b) => b.nodes.length - a.nodes.length || a.label.localeCompare(b.label));
  const radii = ranked.map((cluster) => clusterRadius(cluster, edgeLength));
  const maxRadius = Math.max(112, ...radii);
  const columns = Math.max(1, Math.ceil(Math.sqrt(ranked.length * 1.05)));
  const rows = Math.ceil(ranked.length / columns);
  const cellX = maxRadius * 2 + Math.max(112, edgeLength * 0.38);
  const cellY = maxRadius * 2 + Math.max(104, edgeLength * 0.34);
  const placements = new Map<string, { x: number; y: number; radius: number }>();

  ranked.forEach((cluster, index) => {
    const row = Math.floor(index / columns);
    const col = index % columns;
    placements.set(cluster.id, {
      x: (col - (columns - 1) / 2) * cellX + (row % 2 ? cellX * 0.14 : 0),
      y: (row - (rows - 1) / 2) * cellY,
      radius: radii[index],
    });
  });

  return placements;
}

function nodePosition(index: number, total: number, placement: { x: number; y: number; radius: number }) {
  if (total === 1) return { x: placement.x, y: placement.y };
  const angle = index * Math.PI * (3 - Math.sqrt(5));
  const usableRadius = Math.max(48, placement.radius - 72);
  const radius = total <= 8
    ? Math.min(usableRadius, 50 + total * 5)
    : Math.min(usableRadius, Math.sqrt((index + 0.5) / total) * usableRadius);
  return {
    x: placement.x + Math.cos(angle) * radius,
    y: placement.y + Math.sin(angle) * radius,
  };
}

function unclusteredNodePositions(nodes: GraphNode[], edges: GraphEdge[], edgeLength: number) {
  const positions = new Map<string, { x: number; y: number; vx: number; vy: number }>();
  const indexById = new Map(nodes.map((node, index) => [node.id, index]));
  const links = edges.flatMap((edge) => {
    const source = indexById.get(edge.source);
    const target = indexById.get(edge.target);
    return source === undefined || target === undefined ? [] : [{ source, target, weight: edge.weight || 0.45 }];
  });
  const baseRadius = Math.max(220, Math.sqrt(nodes.length) * 46);

  nodes.forEach((node, index) => {
    const angle = index * Math.PI * (3 - Math.sqrt(5));
    const radius = Math.sqrt(index + 1) * 24;
    positions.set(node.id, {
      x: Math.cos(angle) * radius,
      y: Math.sin(angle) * radius,
      vx: 0,
      vy: 0,
    });
  });

  for (let iteration = 0; iteration < 180; iteration += 1) {
    for (let a = 0; a < nodes.length; a += 1) {
      const source = positions.get(nodes[a].id);
      if (!source) continue;
      for (let b = a + 1; b < nodes.length; b += 1) {
        const target = positions.get(nodes[b].id);
        if (!target) continue;
        const dx = target.x - source.x || 0.01;
        const dy = target.y - source.y || 0.01;
        const distanceSq = Math.max(784, dx * dx + dy * dy);
        const force = 6200 / distanceSq;
        const distance = Math.sqrt(distanceSq);
        const fx = (dx / distance) * force;
        const fy = (dy / distance) * force;
        source.vx -= fx;
        source.vy -= fy;
        target.vx += fx;
        target.vy += fy;
      }
    }

    links.forEach((link) => {
      const source = positions.get(nodes[link.source].id);
      const target = positions.get(nodes[link.target].id);
      if (!source || !target) return;
      const dx = target.x - source.x || 0.01;
      const dy = target.y - source.y || 0.01;
      const distance = Math.max(1, Math.sqrt(dx * dx + dy * dy));
      const desired = Math.max(82, Math.min(152, edgeLength * (0.42 + link.weight * 0.14)));
      const force = (distance - desired) * 0.012;
      const fx = (dx / distance) * force;
      const fy = (dy / distance) * force;
      source.vx += fx;
      source.vy += fy;
      target.vx -= fx;
      target.vy -= fy;
    });

    nodes.forEach((node) => {
      const position = positions.get(node.id);
      if (!position) return;
      position.vx += -position.x / baseRadius * 0.05;
      position.vy += -position.y / baseRadius * 0.05;
      position.vx *= 0.8;
      position.vy *= 0.8;
      position.x += position.vx;
      position.y += position.vy;
    });
  }

  return new Map(nodes.map((node) => {
    const position = positions.get(node.id);
    return [node.id, { x: position?.x || 0, y: position?.y || 0 }];
  }));
}

function datumData<T>(datum: NodeData | EdgeData | ComboData) {
  return (datum.data || {}) as T;
}

function datumPosition(datum: NodeData | ComboData) {
  return (datum.style || {}) as { x?: number; y?: number };
}

function buildGraphData(
  nodes: GraphNode[],
  edges: GraphEdge[],
  clusters: ClusterSummary[],
  nodeCluster: Map<string, string>,
  edgeLength: number,
): GraphData {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const placements = clusterPlacements(clusters, edgeLength);
  const nodePositions = clusters.length
    ? new Map<string, { x: number; y: number }>()
    : unclusteredNodePositions(nodes, edges, edgeLength);

  clusters.forEach((cluster) => {
    const placement = placements.get(cluster.id);
    if (!placement) return;
    cluster.nodes.forEach((node, index) => {
      nodePositions.set(node.id, nodePosition(index, cluster.nodes.length, placement));
    });
  });

  return {
    combos: clusters.map((cluster) => {
      const placement = placements.get(cluster.id);
      return {
        id: cluster.id,
        type: "circle",
        style: {
          x: placement?.x,
          y: placement?.y,
        },
        data: {
          cluster,
          color: cluster.color,
          label: `${cluster.label}\n${cluster.nodes.length} records`,
          radius: placement?.radius || clusterRadius(cluster, edgeLength),
        } satisfies ComboDatumData,
      };
    }),
    nodes: nodes.map((node) => {
      const position = nodePositions.get(node.id);
      return {
        id: node.id,
        type: "circle",
        combo: nodeCluster.get(node.id),
        style: {
          x: position?.x,
          y: position?.y,
        },
        data: {
          record: node,
          color: nodeColor(node),
          label: shortLabel(node.label || formatRecordKind(node.record_kind), clusters.length ? 30 : 42),
          size: nodeSize(node, clusters.length === 0),
        } satisfies NodeDatumData,
      };
    }),
    edges: edges.flatMap((edge, index) => {
      if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return [];
      return [{
        id: edgeKey(edge, index),
        source: edge.source,
        target: edge.target,
        type: "cubic",
        data: {
          edge,
          color: edgeColor(edge),
          label: relationshipLabel(edge),
          width: edgeWidth(edge),
        } satisfies EdgeDatumData,
      }];
    }),
  };
}

function graphOptions(container: HTMLDivElement): GraphOptions {
  return {
    container,
    autoResize: true,
    background: "transparent",
    autoFit: { type: "view", options: { when: "always" } },
    padding: 72,
    zoomRange: [0.18, 2.6],
    data: { nodes: [], edges: [], combos: [] },
    behaviors: [
      { type: "drag-canvas" },
      { type: "zoom-canvas", sensitivity: 0.6 },
      { type: "drag-element", dropEffect: "move", hideEdge: "none" },
      { type: "click-select", degree: 1, state: "selected" },
    ],
    transforms: [{ type: "process-parallel-edges" }],
    node: {
      type: "circle",
      style: (datum) => {
        const data = datumData<NodeDatumData>(datum);
        const position = datumPosition(datum);
        return {
          x: position.x,
          y: position.y,
          size: data.size,
          fill: data.color,
          fillOpacity: 0.92,
          stroke: "#0f172a",
          lineWidth: 2,
          shadowColor: alphaColor(data.color, 0.38),
          shadowBlur: 18,
          halo: true,
          haloStroke: data.color,
          haloStrokeOpacity: 0.14,
          haloLineWidth: 12,
          label: false,
          labelText: data.label,
          labelPlacement: "bottom",
          labelFill: "#f8fafc",
          labelFontSize: 9.5,
          labelFontWeight: 700,
          labelMaxWidth: 116,
          labelBackground: true,
          labelBackgroundFill: alphaColor("#020617", 0.82),
          labelBackgroundOpacity: 0.86,
          labelBackgroundRadius: 5,
          labelBackgroundPadding: [3, 5, 3, 5],
        };
      },
      state: {
        selected: {
          stroke: "#ffffff",
          lineWidth: 3.5,
          haloStroke: "#ffffff",
          haloStrokeOpacity: 0.24,
          haloLineWidth: 18,
          label: true,
          labelBackgroundOpacity: 0.96,
        },
      },
    },
    combo: {
      type: "circle",
      style: (datum) => {
        const data = datumData<ComboDatumData>(datum);
        const count = data.cluster.nodes.length;
        const position = datumPosition(datum);
        return {
          x: position.x,
          y: position.y,
          size: data.radius * 2,
          padding: count > 12 ? 24 : count > 6 ? 20 : 16,
          fill: alphaColor(data.color, 0.1),
          fillOpacity: 0.92,
          stroke: data.color,
          strokeOpacity: 0.48,
          lineWidth: 1.8,
          lineDash: [7, 6],
          shadowColor: alphaColor(data.color, 0.16),
          shadowBlur: 32,
          label: true,
          labelText: data.label,
          labelPlacement: "top",
          labelFill: "#eef6ff",
          labelFontSize: 17,
          labelFontWeight: 800,
          labelLineHeight: 20,
          labelMaxWidth: 220,
          labelBackground: true,
          labelBackgroundFill: alphaColor("#020617", 0.84),
          labelBackgroundOpacity: 0.92,
          labelBackgroundRadius: 999,
          labelBackgroundPadding: [5, 9, 5, 9],
          collapsedMarker: false,
        };
      },
      state: {
        selected: {
          strokeOpacity: 0.9,
          lineWidth: 2.8,
          shadowBlur: 46,
          fillOpacity: 1,
        },
      },
    },
    edge: {
      type: "cubic",
      style: (datum) => {
        const data = datumData<EdgeDatumData>(datum);
        return {
          stroke: data.color,
          strokeOpacity: 0.18,
          lineWidth: data.width,
          endArrow: true,
          label: false,
          labelText: data.label,
          labelPlacement: "center",
          labelFill: "#f8fafc",
          labelFontSize: 9,
          labelFontWeight: 700,
          labelBackground: true,
          labelBackgroundFill: alphaColor("#020617", 0.88),
          labelBackgroundOpacity: 0.92,
          labelBackgroundPadding: [2, 5, 2, 5],
        };
      },
      state: {
        selected: {
          strokeOpacity: 0.96,
          lineWidth: 3.7,
          label: true,
        },
      },
    },
  };
}

export default function GraphExplorer({ onRecordClick }: GraphExplorerProps) {
  const graphRef = useRef<Graph | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeByIdRef = useRef(new Map<string, GraphNode>());
  const edgeByKeyRef = useRef(new Map<string, GraphEdge>());
  const clusterByIdRef = useRef(new Map<string, ClusterSummary>());
  const [graph, setGraph] = useState<GraphState>({
    nodes: [],
    edges: [],
    totalRecords: 0,
    truncated: false,
  });
  const [clusterBy, setClusterBy] = useState<ClusterBy>("semantic");
  const [maxNodes, setMaxNodes] = useState(DEFAULT_MAX_NODES);
  const [edgeLength, setEdgeLength] = useState(DEFAULT_EDGE_LENGTH);
  const [selected, setSelected] = useState<Selection>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const { clusters, nodeCluster } = useMemo(
    () => buildClusters(graph.nodes, graph.edges, clusterBy),
    [clusterBy, graph.edges, graph.nodes],
  );

  const nodeById = useMemo(
    () => new Map(graph.nodes.map((node) => [node.id, node])),
    [graph.nodes],
  );

  const edgeByKey = useMemo(() => {
    const map = new Map<string, GraphEdge>();
    graph.edges.forEach((edge, index) => map.set(edgeKey(edge, index), edge));
    return map;
  }, [graph.edges]);

  const clusterById = useMemo(
    () => new Map(clusters.map((cluster) => [cluster.id, cluster])),
    [clusters],
  );

  const graphData = useMemo(
    () => buildGraphData(graph.nodes, graph.edges, clusters, nodeCluster, edgeLength),
    [clusters, edgeLength, graph.edges, graph.nodes, nodeCluster],
  );
  const relationCounts = useMemo(() => {
    const counts = new Map<GraphEdge["kind"], number>();
    graph.edges.forEach((edge) => counts.set(edge.kind, (counts.get(edge.kind) || 0) + 1));
    return counts;
  }, [graph.edges]);
  const recordKindCounts = useMemo(() => {
    const counts = new Map<string, number>();
    graph.nodes.forEach((node) => {
      const kind = (node.record_kind || "context_record").toLowerCase();
      counts.set(kind, (counts.get(kind) || 0) + 1);
    });
    return counts;
  }, [graph.nodes]);

  useEffect(() => {
    nodeByIdRef.current = nodeById;
    edgeByKeyRef.current = edgeByKey;
    clusterByIdRef.current = clusterById;
  }, [clusterById, edgeByKey, nodeById]);

  async function loadGraph() {
    setLoading(true);
    setError(null);
    try {
      const response = await api.queryGraph({
        max_nodes: maxNodes,
        max_edges: Math.max(maxNodes * 3, 240),
        connected_only: false,
      });

      setGraph({
        nodes: response.nodes,
        edges: response.edges,
        totalRecords: response.total_records,
        truncated: Boolean(response.truncated),
      });
      setSelected(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to load graph");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadGraph();
    // Initial load only. Refresh button applies max-node changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!containerRef.current || graphRef.current) return;

    const g6 = new Graph(graphOptions(containerRef.current));
    const handleNodeClick = (event: IElementEvent) => {
      const node = nodeByIdRef.current.get(String(event.target.id));
      if (node) setSelected({ kind: "node", node });
    };
    const handleComboClick = (event: IElementEvent) => {
      const cluster = clusterByIdRef.current.get(String(event.target.id));
      if (cluster) setSelected({ kind: "cluster", cluster });
    };
    const handleEdgeClick = (event: IElementEvent) => {
      const key = String(event.target.id);
      const edge = edgeByKeyRef.current.get(key);
      if (edge) setSelected({ kind: "edge", edge });
    };
    const handleCanvasClick = (event: IElementEvent) => {
      if (!event.target?.id) setSelected(null);
    };

    g6.on(NodeEvent.CLICK, handleNodeClick);
    g6.on(ComboEvent.CLICK, handleComboClick);
    g6.on(EdgeEvent.CLICK, handleEdgeClick);
    g6.on(CanvasEvent.CLICK, handleCanvasClick);
    graphRef.current = g6;

    return () => {
      g6.off(NodeEvent.CLICK, handleNodeClick);
      g6.off(ComboEvent.CLICK, handleComboClick);
      g6.off(EdgeEvent.CLICK, handleEdgeClick);
      g6.off(CanvasEvent.CLICK, handleCanvasClick);
      g6.destroy();
      graphRef.current = null;
    };
    // The G6 instance is intentionally created once; data/options update in later effects.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    const g6 = graphRef.current;
    if (!g6 || g6.destroyed) return;
    g6.setData(graphData);
    const renderId = window.setTimeout(() => {
      if (g6.destroyed || graphRef.current !== g6) return;
      void g6.render()
        .then(() => {
          if (!g6.destroyed && graphRef.current === g6) void g6.fitView({ when: "always" }, { duration: 260 });
        })
        .catch(() => undefined);
    }, 0);
    return () => window.clearTimeout(renderId);
  }, [graphData]);

  function fitGraph() {
    const g6 = graphRef.current;
    if (!g6 || g6.destroyed) return;
    void g6.fitView({ when: "always" }, { duration: 260 });
  }

  function relayoutGraph() {
    const g6 = graphRef.current;
    if (!g6 || g6.destroyed) return;
    void g6.render().then(() => {
      if (!g6.destroyed) void g6.fitView({ when: "always" }, { duration: 260 });
    }).catch(() => undefined);
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#050b16] text-slate-100">
      <div className="flex flex-col gap-3 border-b border-white/10 bg-[#07101f]/95 px-4 py-3 shadow-[0_1px_0_rgba(255,255,255,0.04)] backdrop-blur lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-sm font-semibold text-white">Graph</h1>
            <span className="rounded-full border border-sky-400/20 bg-sky-400/10 px-2 py-0.5 text-[11px] font-medium text-sky-200">
              {clusterBy === "semantic" ? "Topic view" : "Clustering off"}
            </span>
            <span className="rounded-full border border-teal-300/20 bg-teal-300/10 px-2 py-0.5 text-[11px] font-medium text-teal-100">
              {clusterBy === "semantic" ? "Cluster map" : "Record map"}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <Metric label="nodes" value={graph.nodes.length} />
            <Metric label="edges" value={graph.edges.length} />
            {clusterBy === "semantic" && <Metric label="clusters" value={clusters.length} />}
            <Metric label="records" value={graph.totalRecords} />
            {graph.truncated && <span className="text-amber-700">showing first {graph.nodes.length}</span>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2 lg:justify-end">
          <div className="flex h-9 items-center gap-1 rounded-full border border-white/10 bg-white/[0.045] p-1 shadow-sm">
            {CLUSTER_OPTIONS.map((option) => (
              <button
                key={option.value}
                type="button"
                title={option.hint}
                onClick={() => {
                  setClusterBy(option.value);
                  setSelected(null);
                }}
                className={`h-7 rounded-full px-3 text-xs font-medium transition ${
                  clusterBy === option.value
                    ? "bg-sky-400 text-slate-950 shadow-[0_0_18px_rgba(56,189,248,0.28)]"
                    : "text-slate-300 hover:bg-white/8 hover:text-white"
                }`}
              >
                {option.label}
              </button>
            ))}
          </div>
          <label className="flex h-9 items-center gap-2 rounded-full border border-white/10 bg-white/[0.045] px-3 text-xs text-slate-300 shadow-sm">
            Nodes
            <input
              type="number"
              min={40}
              max={400}
              value={maxNodes}
              onChange={(event) => setMaxNodes(Number(event.target.value))}
              className="w-16 bg-transparent text-sm text-white outline-none"
            />
          </label>
          <label className="flex h-9 items-center gap-2 rounded-full border border-white/10 bg-white/[0.045] px-3 text-xs text-slate-300 shadow-sm">
            Spacing
            <input
              type="range"
              min={130}
              max={340}
              value={edgeLength}
              onChange={(event) => setEdgeLength(Number(event.target.value))}
              className="w-24"
            />
          </label>
          <button type="button" onClick={loadGraph} className="rounded-full border border-white/10 bg-white/[0.07] px-3 py-2 text-sm font-medium text-slate-100 shadow-sm transition hover:bg-white/12 disabled:opacity-60" disabled={loading}>
            {loading ? "Loading" : "Refresh"}
          </button>
          <button type="button" onClick={fitGraph} className="rounded-full border border-white/10 bg-white/[0.07] px-3 py-2 text-sm font-medium text-slate-100 shadow-sm transition hover:bg-white/12">
            Fit
          </button>
          <button type="button" onClick={relayoutGraph} className="rounded-full border border-white/10 bg-white/[0.07] px-3 py-2 text-sm font-medium text-slate-100 shadow-sm transition hover:bg-white/12">
            Layout
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_380px]">
        <div className="relative min-h-[520px] min-w-0 overflow-hidden bg-[radial-gradient(circle_at_18%_12%,rgba(59,130,246,0.18),transparent_30%),radial-gradient(circle_at_78%_24%,rgba(45,212,191,0.13),transparent_28%),radial-gradient(circle_at_46%_82%,rgba(168,85,247,0.09),transparent_34%),#020617]">
          <div className="pointer-events-none absolute inset-0 z-0 bg-[linear-gradient(rgba(148,163,184,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.05)_1px,transparent_1px)] bg-[size:34px_34px]" />
          <div className="pointer-events-none absolute inset-0 z-[1] bg-[radial-gradient(circle_at_center,transparent,rgba(2,6,23,0.62))]" />
          <div ref={containerRef} className="absolute inset-0 z-[2]" />

          {loading && (
            <div className="absolute left-4 top-4 z-10 rounded-md border border-white/10 bg-slate-950/85 px-3 py-2 text-sm text-slate-200 shadow-sm backdrop-blur">
              Loading graph...
            </div>
          )}

          {error && (
            <div className="absolute left-4 right-4 top-4 z-10 rounded-md border border-red-500/20 bg-red-50 px-3 py-2 text-sm text-red-700 shadow-sm">
              {error}
            </div>
          )}

          {!loading && !error && graph.nodes.length === 0 && (
            <div className="absolute inset-0 z-10 flex items-center justify-center p-8 text-center">
              <div>
                <p className="text-sm font-medium text-white">No graph data found</p>
                <p className="mt-1 text-sm text-slate-400">Ingest records to populate this view.</p>
              </div>
            </div>
          )}
        </div>

        <aside className="min-h-0 overflow-y-auto border-t border-white/10 bg-[#07101f] p-4 shadow-[inset_1px_0_0_rgba(255,255,255,0.03)] lg:border-l lg:border-t-0">
          <DetailsPanel
            selection={selected}
            nodeById={nodeById}
            recordKindCounts={recordKindCounts}
            relationCounts={relationCounts}
            onOpenRecord={onRecordClick}
          />
        </aside>
      </div>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number }) {
  return (
    <span className="rounded-full border border-white/8 bg-white/[0.035] px-2 py-0.5">
      <span className="font-semibold text-white">{value.toLocaleString()}</span> {label}
    </span>
  );
}

function DetailsPanel({
  selection,
  nodeById,
  recordKindCounts,
  relationCounts,
  onOpenRecord,
}: {
  selection: Selection;
  nodeById: Map<string, GraphNode>;
  recordKindCounts: Map<string, number>;
  relationCounts: Map<GraphEdge["kind"], number>;
  onOpenRecord?: (recordId: string) => void;
}) {
  if (!selection) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <div className="mb-3 h-2 w-16 rounded-full bg-gradient-to-r from-sky-400 to-teal-300 shadow-[0_0_22px_rgba(56,189,248,0.35)]" />
        <h2 className="text-sm font-semibold text-white">Inspect the graph</h2>
        <p className="mt-2 text-sm leading-6 text-slate-400">
          Click a cluster to understand a topic. Click a record node to read the
          memory. Click a relationship to inspect why two records are linked.
        </p>
        <div className="mt-4 grid grid-cols-3 gap-2 text-center text-[11px] text-slate-400">
          <span className="rounded-lg border border-white/10 bg-white/[0.035] px-2 py-2">clusters</span>
          <span className="rounded-lg border border-white/10 bg-white/[0.035] px-2 py-2">records</span>
          <span className="rounded-lg border border-white/10 bg-white/[0.035] px-2 py-2">links</span>
        </div>
        {recordKindCounts.size > 0 && (
          <div className="mt-5">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Record types</div>
            <div className="grid grid-cols-2 gap-2">
              {Array.from(recordKindCounts.entries()).sort((a, b) => b[1] - a[1]).map(([kind, count]) => (
                <div key={kind} className="rounded-xl border border-white/10 bg-white/[0.035] px-3 py-2">
                  <div className="flex items-center gap-2">
                    <span
                      className="h-2.5 w-2.5 rounded-full shadow-[0_0_14px_currentColor]"
                      style={{ backgroundColor: KIND_COLORS[kind] || "#64748b", color: KIND_COLORS[kind] || "#64748b" }}
                    />
                    <span className="text-xs font-semibold text-slate-100">{formatRecordKind(kind)}</span>
                  </div>
                  <div className="mt-1 text-[11px] text-slate-500">{count} records</div>
                </div>
              ))}
            </div>
          </div>
        )}
        {relationCounts.size > 0 && (
          <div className="mt-5">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Relationship catalog</div>
            <div className="space-y-2">
              {Object.entries(RELATION_DESCRIPTIONS).flatMap(([kind, description]) => {
                const typedKind = kind as GraphEdge["kind"];
                const count = relationCounts.get(typedKind) || 0;
                if (!count) return [];
                return (
                  <div key={kind} className="rounded-xl border border-white/10 bg-white/[0.035] p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2">
                        <span className="h-2.5 w-2.5 rounded-full shadow-[0_0_14px_currentColor]" style={{ backgroundColor: RELATION_COLORS[typedKind], color: RELATION_COLORS[typedKind] }} />
                        <span className="text-sm font-semibold text-slate-100">{humanizeToken(kind)}</span>
                      </div>
                      <span className="rounded-full border border-white/10 bg-white/[0.045] px-2 py-0.5 text-[11px] text-slate-400">
                        {count}
                      </span>
                    </div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">{description}</p>
                  </div>
                );
              })}
            </div>
          </div>
        )}
      </div>
    );
  }

  if (selection.kind === "cluster") {
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
          <div className="mb-3 h-2 w-20 rounded-full shadow-[0_0_22px_currentColor]" style={{ backgroundColor: selection.cluster.color }} />
          <h2 className="text-base font-semibold leading-6 text-white">{selection.cluster.label}</h2>
          <p className="mt-1 text-xs text-slate-400">
            {selection.cluster.lensLabel} - {selection.cluster.nodes.length} nodes - {selection.cluster.edges.length} connected edges
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.045] p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Cluster summary</div>
          <p className="mt-2 text-sm leading-6 text-slate-200">{selection.cluster.description}</p>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <Detail label="Main types" value={selection.cluster.topKinds.join(", ") || "Unknown"} />
          <Detail label="Projects" value={selection.cluster.topProjects.join(", ") || "Unscoped"} />
        </div>

        {selection.cluster.representativeTitles.length > 0 && (
          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">Representative records</div>
            <ul className="space-y-1.5 text-sm leading-5 text-slate-300">
              {selection.cluster.representativeTitles.map((title) => (
                <li key={title} className="rounded-lg bg-white/[0.045] px-3 py-2">{title}</li>
              ))}
            </ul>
          </div>
        )}
      </div>
    );
  }

  if (selection.kind === "edge") {
    const source = nodeById.get(selection.edge.source);
    const target = nodeById.get(selection.edge.target);
    const label = relationshipDetailLabel(selection.edge);
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
          <div className="mb-3 h-2 w-16 rounded-full shadow-[0_0_22px_currentColor]" style={{ backgroundColor: edgeColor(selection.edge) }} />
          <h2 className="text-base font-semibold text-white">{relationshipLabel(selection.edge)}</h2>
          <p className="mt-1 text-xs text-slate-400">{RELATION_DESCRIPTIONS[selection.edge.kind]}</p>
        </div>
        <div className="rounded-2xl border border-white/10 bg-white/[0.045] p-4 text-sm leading-6 text-slate-300">
          <p><span className="text-slate-500">From:</span> {source?.label || selection.edge.source}</p>
          <p><span className="text-slate-500">To:</span> {target?.label || selection.edge.target}</p>
          {label && <p className="mt-3"><span className="text-slate-500">Label:</span> {label}</p>}
          {selection.edge.rationale && <p className="mt-3 text-slate-200">{selection.edge.rationale}</p>}
        </div>
        <div className="grid grid-cols-2 gap-3 text-xs">
          <Detail label="Type" value={relationshipLabel(selection.edge)} />
          <Detail label="Weight" value={(selection.edge.weight ?? 0).toFixed(2)} />
          <Detail label="Status" value={selection.edge.status || "active"} />
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <div className="mb-3 h-2 w-16 rounded-full shadow-[0_0_22px_currentColor]" style={{ backgroundColor: nodeColor(selection.node) }} />
        <h2 className="text-base font-semibold leading-6 text-white">{selection.node.label || "Untitled record"}</h2>
        <p className="mt-1 text-xs text-slate-400">
          {formatRecordKind(selection.node.record_kind)} - {formatScopeLabel(selection.node.project)} - {compactDate(selection.node.updated_at)}
        </p>
      </div>

      {(selection.node.body || selection.node.summary) && (
        <div className="rounded-2xl border border-white/10 bg-white/[0.045] p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">Memory</div>
          <p className="mt-2 whitespace-pre-wrap text-sm leading-6 text-slate-200">
            {truncate(selection.node.body || selection.node.summary, 760)}
          </p>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 text-xs">
        <Detail label="Type" value={formatRecordKind(selection.node.record_kind)} />
        <Detail label="Confidence" value={`${Math.round((selection.node.confidence || 0) * 100)}%`} />
        <Detail label="Status" value={selection.node.status || "active"} />
        <Detail label="Project" value={formatScopeLabel(selection.node.project)} />
      </div>

      {selection.node.tags && selection.node.tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {selection.node.tags.slice(0, 10).map((tag) => (
            <span key={tag} className="rounded-full border border-white/10 bg-white/[0.045] px-2 py-1 text-[11px] text-slate-300">{tag}</span>
          ))}
        </div>
      )}

      {onOpenRecord && (
        <button
          type="button"
          onClick={() => onOpenRecord(selection.node.id)}
          className="w-full rounded-xl border border-sky-300/20 bg-sky-300/10 px-3 py-2 text-sm font-medium text-sky-100 transition hover:bg-sky-300/15"
        >
          Open record
        </button>
      )}
    </div>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-3">
      <div className="text-[11px] uppercase tracking-wide text-slate-500">{label}</div>
      <div className="mt-1 break-words text-sm font-medium text-slate-200">{value}</div>
    </div>
  );
}
