"use client";

import { useEffect, useMemo, useRef, useState, type PointerEvent as ReactPointerEvent } from "react";
import cytoscape, {
  type Core,
  type ElementDefinition,
  type EventObject,
  type StylesheetJson,
} from "cytoscape";
import fcose from "cytoscape-fcose";
import { api } from "@/lib/api";
import { formatRecordKind, formatScopeLabel, humanizeToken } from "@/lib/labels";
import type { GraphEdge, GraphNode } from "@/lib/types";

const cytoscapeRuntime = cytoscape as typeof cytoscape & {
  lerimFcoseRegistered?: boolean;
};

if (!cytoscapeRuntime.lerimFcoseRegistered) {
  cytoscapeRuntime.use(fcose);
  cytoscapeRuntime.lerimFcoseRegistered = true;
}

interface GraphExplorerProps {
  onRecordClick?: (recordId: string) => void;
}

type ClusterBy = "semantic" | "community" | "project" | "type";
type Selection =
  | { kind: "node"; node: GraphNode }
  | { kind: "edge"; edge: GraphEdge; key: string }
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
  key: string;
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

type ClusterOverlay = {
  id: string;
  label: string;
  color: string;
  x: number;
  y: number;
  width: number;
  height: number;
  count: number;
};

type ClusterDragSnapshot = {
  clusterId: string;
  positions: Array<{ id: string; x: number; y: number }>;
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
  { value: "community", label: "Community", hint: "Linked groups" },
  { value: "project", label: "Project", hint: "Project scopes" },
  { value: "type", label: "Type", hint: "Record kinds" },
];

function edgeKey(edge: GraphEdge, index: number) {
  return edge.id || `${edge.source}:${edge.kind}:${edge.target}:${index}`;
}

function relationshipLabel(edge: GraphEdge) {
  return edge.label?.trim() || humanizeToken(edge.kind);
}

function shortLabel(value?: string | null, max = 34) {
  const label = value?.trim() || "Untitled";
  return label.length > max ? `${label.slice(0, max - 1)}...` : label;
}

function nodeColor(node: GraphNode) {
  return KIND_COLORS[node.record_kind || ""] || "#475569";
}

function edgeColor(edge: GraphEdge) {
  return RELATION_COLORS[edge.kind] || "#64748b";
}

function clusterValue(node: GraphNode, clusterBy: ClusterBy) {
  if (clusterBy === "semantic") {
    return node.semantic_cluster || node.community_cluster || node.project || "unclustered";
  }
  if (clusterBy === "community") {
    return node.community_cluster || node.semantic_cluster || node.project || "unclustered";
  }
  if (clusterBy === "project") return node.project || "unscoped";
  return node.record_kind || "unknown";
}

function clusterLabel(value: string, clusterBy: ClusterBy) {
  if (clusterBy === "type") return formatRecordKind(value);
  if (clusterBy === "project") return formatScopeLabel(value);
  if (value === "unclustered" || value === "semantic_unclustered") return "Unclustered";
  if (value === "community_unlinked") return "Unlinked";
  return humanizeToken(value.replace(/^semantic_/, "Topic ").replace(/^community_/, "Group "));
}

function clusterId(value: string) {
  return `cluster:${encodeURIComponent(value)}`;
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

function clusterDescription(nodes: GraphNode[]) {
  const titles = representativeTitles(nodes);
  const kinds = rankedValues(nodes.map((node) => node.record_kind), 2).map(formatRecordKind);
  const projects = rankedValues(nodes.map((node) => node.project), 2).map(formatScopeLabel);

  const parts = [];
  if (kinds.length) parts.push(kinds.join(" / "));
  if (projects.length) parts.push(projects.join(" + "));
  if (titles.length) parts.push(titles.slice(0, 2).join(" · "));
  return parts.join(" · ") || "No summary available";
}

function clusterTitle(cluster: ClusterSummary, clusterBy: ClusterBy) {
  if (clusterBy === "project" || clusterBy === "type") return cluster.label;
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
    54,
  );
}

function buildClusters(nodes: GraphNode[], edges: GraphEdge[], clusterBy: ClusterBy) {
  const clusters = new Map<string, ClusterSummary>();
  const nodeCluster = new Map<string, string>();
  const nodeIds = new Set(nodes.map((node) => node.id));

  nodes.forEach((node) => {
    const key = clusterValue(node, clusterBy);
    const id = clusterId(key);
    const existing = clusters.get(id);
    const cluster =
      existing ||
      {
        id,
        key,
        label: clusterLabel(key, clusterBy),
        lensLabel: clusterLabel(key, clusterBy),
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
    if (targetCluster && targetCluster !== sourceCluster) {
      clusters.get(targetCluster)?.edges.push(edge);
    }
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
      label: clusterTitle(summarized, clusterBy),
    };
  });

  return { clusters: summarizedClusters, nodeCluster };
}

function graphStyles(): StylesheetJson {
  return [
    {
      selector: "node[cluster]",
      style: {
        "background-color": "data(color)",
        "background-opacity": 0.01,
        "border-color": "data(color)",
        "border-opacity": 0,
        "border-style": "solid",
        "border-width": 0,
        color: "#dbeafe",
        "compound-sizing-wrt-labels": "include",
        "font-size": 11,
        "font-weight": 800,
        label: "",
        padding: 54,
        shape: "ellipse",
        "text-background-color": "#020617",
        "text-background-opacity": 0.78,
        "text-background-padding": 4,
        "text-border-color": "data(color)",
        "text-border-opacity": 0.35,
        "text-border-width": 1,
        "text-halign": "center",
        "text-margin-y": -14,
        "text-max-width": 170,
        "text-valign": "top",
        "text-wrap": "wrap",
        "underlay-color": "data(color)",
        "underlay-opacity": 0,
        "underlay-padding": 0,
      },
    },
    {
      selector: "node[cluster]:selected",
      style: {
        "background-opacity": 0.02,
        "border-opacity": 0,
        "border-width": 0,
        "font-size": 12,
        "text-background-opacity": 0.82,
        "underlay-opacity": 0,
        "underlay-padding": 0,
      },
    },
    {
      selector: "node[record]",
      style: {
        "background-color": "data(color)",
        "background-opacity": 0.96,
        "border-color": "#0f172a",
        "border-opacity": 0.88,
        "border-width": 1.75,
        color: "#f8fafc",
        "font-size": 9.5,
        "font-weight": 700,
        height: "data(size)",
        label: "data(label)",
        "min-zoomed-font-size": 7,
        "overlay-opacity": 0,
        shape: "ellipse",
        "text-background-color": "#020617",
        "text-background-opacity": 0.78,
        "text-background-padding": 3,
        "text-margin-y": 6,
        "text-max-width": 112,
        "text-valign": "bottom",
        "text-wrap": "ellipsis",
        width: "data(size)",
      },
    },
    {
      selector: "node[record]:selected",
      style: {
        "border-color": "#f8fafc",
        "border-width": 3.5,
        "font-size": 11,
        "text-background-opacity": 0.92,
        "text-max-width": 160,
      },
    },
    {
      selector: "node[record].focused",
      style: {
        "border-color": "#f8fafc",
        "border-opacity": 0.9,
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        "font-size": 9.5,
        "font-weight": 700,
        label: "",
        "line-color": "data(color)",
        opacity: 0.32,
        "target-arrow-color": "data(color)",
        "target-arrow-shape": "triangle",
        color: "#e2e8f0",
        "text-background-color": "#020617",
        "text-background-opacity": 0.84,
        "text-background-padding": 2,
        "text-border-color": "data(color)",
        "text-border-opacity": 0.2,
        "text-border-width": 1,
        "text-margin-y": -5,
        "text-max-width": 128,
        "text-rotation": "autorotate",
        "text-wrap": "ellipsis",
        width: "data(width)",
      },
    },
    {
      selector: "edge.focused",
      style: {
        label: "",
        opacity: 0.92,
        "text-background-opacity": 0.9,
        width: "mapData(width, 1.2, 3.4, 2, 4.2)",
      },
    },
    {
      selector: "edge:selected",
      style: {
        "font-size": 10,
        label: "data(label)",
        "line-color": "data(color)",
        opacity: 1,
        "target-arrow-color": "data(color)",
        color: "#ffffff",
        "text-background-color": "#0f172a",
        "text-background-opacity": 0.96,
        "text-background-padding": 3,
        "text-max-width": 160,
        "text-rotation": "autorotate",
        "text-wrap": "ellipsis",
        width: 4,
      },
    },
    {
      selector: ".faded",
      style: {
        opacity: 0.16,
      },
    },
    {
      selector: ".focused",
      style: {
        opacity: 1,
      },
    },
  ] as unknown as StylesheetJson;
}

function graphElements(
  nodes: GraphNode[],
  edges: GraphEdge[],
  clusters: ClusterSummary[],
  nodeCluster: Map<string, string>,
): ElementDefinition[] {
  const nodeIds = new Set(nodes.map((node) => node.id));
  const elements: ElementDefinition[] = clusters.map((cluster) => ({
    data: {
      id: cluster.id,
      label: `${cluster.label}\n${cluster.nodes.length} records`,
      color: cluster.color,
      cluster: true,
    },
    selectable: true,
  }));

  nodes.forEach((node) => {
    elements.push({
      data: {
        id: node.id,
        parent: nodeCluster.get(node.id),
        label: shortLabel(node.label || formatRecordKind(node.record_kind)),
        color: nodeColor(node),
        record: true,
        size: Math.max(26, Math.min(44, 28 + (node.confidence || 0.5) * 14)),
      },
      selectable: true,
    });
  });

  edges.forEach((edge, index) => {
    if (!nodeIds.has(edge.source) || !nodeIds.has(edge.target)) return;
    elements.push({
      data: {
        id: edgeKey(edge, index),
        source: edge.source,
        target: edge.target,
        label: relationshipLabel(edge),
        color: edgeColor(edge),
        width: Math.max(1.2, Math.min(3.4, 1.2 + (edge.weight || 0.45) * 2.2)),
      },
      selectable: true,
    });
  });

  return elements;
}

function bubbleRadius(cluster: ClusterSummary) {
  return Math.max(104, Math.min(168, 84 + Math.sqrt(cluster.nodes.length) * 21));
}

function keepRecordInsideCluster(
  cy: Core | null,
  nodeId: string,
  clustersById: Map<string, ClusterSummary>,
) {
  if (!cy || cy.destroyed()) return;
  const node = cy.getElementById(nodeId);
  if (!node.length) return;
  const cluster = Array.from(clustersById.values()).find((candidate) =>
    candidate.nodes.some((record) => record.id === nodeId),
  );
  if (!cluster || cluster.nodes.length < 2) return;
  const siblings = cluster.nodes
    .map((record) => cy.getElementById(record.id))
    .filter((candidate) => candidate.length && candidate.id() !== nodeId);
  if (!siblings.length) return;
  const center = siblings.reduce(
    (acc, sibling) => {
      const position = sibling.position();
      acc.x += position.x;
      acc.y += position.y;
      return acc;
    },
    { x: 0, y: 0 },
  );
  center.x /= siblings.length;
  center.y /= siblings.length;
  const position = node.position();
  const deltaX = position.x - center.x;
  const deltaY = position.y - center.y;
  const distance = Math.hypot(deltaX, deltaY);
  const maxDistance = Math.max(38, bubbleRadius(cluster) * 0.48);
  if (distance <= maxDistance || distance < 1) return;
  node.position({
    x: center.x + (deltaX / distance) * maxDistance,
    y: center.y + (deltaY / distance) * maxDistance,
  });
}

function measureClusterOverlays(
  cy: Core | null,
  clustersById: Map<string, ClusterSummary>,
) {
  if (!cy || cy.destroyed()) return [];
  return Array.from(clustersById.values()).flatMap((cluster) => {
    const element = cy.getElementById(cluster.id);
    if (!element.length) return [];
    const children = element.children("[record]");
    if (!children.length) return [];
    const box = children.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
    if (!Number.isFinite(box.w) || !Number.isFinite(box.h)) return [];
    const width = Math.max(112, box.w + 34);
    const height = Math.max(96, box.h + 34);
    return [{
      id: cluster.id,
      label: shortLabel(cluster.label, 42),
      color: cluster.color,
      x: box.x1 + box.w / 2 - width / 2,
      y: box.y1 + box.h / 2 - height / 2,
      width,
      height,
      count: cluster.nodes.length,
    }];
  });
}

function clusterBoxesOverlap(
  cy: Core | null,
  clusterId: string,
  clustersById: Map<string, ClusterSummary>,
) {
  if (!cy || cy.destroyed()) return false;
  const source = cy.getElementById(clusterId);
  if (!source.length) return false;
  const sourceChildren = source.children("[record]");
  if (!sourceChildren.length) return false;
  const sourceBox = sourceChildren.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
  const margin = 28;
  return Array.from(clustersById.keys()).some((otherId) => {
    if (otherId === clusterId) return false;
    const target = cy.getElementById(otherId);
    if (!target.length) return false;
    const targetChildren = target.children("[record]");
    if (!targetChildren.length) return false;
    const targetBox = targetChildren.renderedBoundingBox({ includeLabels: false, includeOverlays: false });
    return !(
      sourceBox.x2 + margin < targetBox.x1 ||
      sourceBox.x1 - margin > targetBox.x2 ||
      sourceBox.y2 + margin < targetBox.y1 ||
      sourceBox.y1 - margin > targetBox.y2
    );
  });
}

function clusterDragSnapshot(cy: Core | null, clusterId: string): ClusterDragSnapshot | null {
  if (!cy || cy.destroyed()) return null;
  const cluster = cy.getElementById(clusterId);
  if (!cluster.length) return null;
  return {
    clusterId,
    positions: cluster.children("[record]").map((node) => {
      const position = node.position();
      return {
        id: node.id(),
        x: position.x,
        y: position.y,
      };
    }),
  };
}

function restoreClusterSnapshot(cy: Core | null, snapshot: ClusterDragSnapshot | null) {
  if (!cy || cy.destroyed() || !snapshot) return;
  cy.startBatch();
  snapshot.positions.forEach((position) => {
    const node = cy.getElementById(position.id);
    if (node.length) node.position({ x: position.x, y: position.y });
  });
  cy.endBatch();
}

function moveClusterChildren(cy: Core | null, clusterId: string, deltaX: number, deltaY: number) {
  if (!cy || cy.destroyed()) return;
  const cluster = cy.getElementById(clusterId);
  if (!cluster.length) return;
  const children = cluster.children("[record]");
  if (!children.length) return;
  cy.startBatch();
  children.forEach((node) => {
    const position = node.position();
    node.position({ x: position.x + deltaX, y: position.y + deltaY });
  });
  cy.endBatch();
}

function runClusterBubbleLayout(
  cy: Core | null,
  clusters: ClusterSummary[],
) {
  if (!cy || cy.destroyed()) return;
  const sortedClusters = [...clusters].sort((a, b) => {
    const sizeDelta = b.nodes.length - a.nodes.length;
    return sizeDelta || a.label.localeCompare(b.label);
  });
  const columnCount = Math.min(5, Math.max(2, Math.ceil(Math.sqrt(sortedClusters.length * 1.65))));
  const cellWidth = 640;
  const cellHeight = 500;
  const centers = new Map<string, { x: number; y: number; radius: number }>();

  sortedClusters.forEach((cluster, index) => {
    const radius = bubbleRadius(cluster);
    const column = index % columnCount;
    const row = Math.floor(index / columnCount);
    centers.set(cluster.id, {
      x: column * cellWidth + cellWidth / 2,
      y: row * cellHeight + cellHeight / 2,
      radius,
    });
  });

  cy.startBatch();
  sortedClusters.forEach((cluster) => {
    const center = centers.get(cluster.id);
    if (!center) return;
    const records = cluster.nodes
      .map((node) => cy.getElementById(node.id))
      .filter((node) => Boolean(node.length));
    const innerRadius = Math.max(32, center.radius - 58);
    records.forEach((node, index) => {
      if (records.length === 1) {
        node.position({ x: center.x, y: center.y + 16 });
        return;
      }
      const angle = index * 2.399963229728653;
      const distance = innerRadius * Math.sqrt((index + 0.35) / records.length);
      node.position({
        x: center.x + Math.cos(angle) * distance,
        y: center.y + 18 + Math.sin(angle) * distance,
      });
    });
  });
  cy.endBatch();
  cy.fit(undefined, 112);
  cy.center();
}

function runLayout(cy: Core | null, edgeLength: number) {
  if (!cy || cy.destroyed()) return;
  cy.layout({
    name: "fcose",
    animate: true,
    animationDuration: 420,
    edgeElasticity: 0.22,
    fit: true,
    gravity: 0.24,
    idealEdgeLength: edgeLength,
    nestingFactor: 0.36,
    nodeRepulsion: 18000,
    nodeSeparation: 156,
    numIter: 3200,
    packComponents: true,
    padding: 104,
    quality: "default",
    randomize: true,
    tilingPaddingHorizontal: 82,
    tilingPaddingVertical: 82,
  } as cytoscape.LayoutOptions).run();
}

export default function GraphExplorer({ onRecordClick }: GraphExplorerProps) {
  const cyRef = useRef<Core | null>(null);
  const containerRef = useRef<HTMLDivElement | null>(null);
  const nodeByIdRef = useRef(new Map<string, GraphNode>());
  const edgeByKeyRef = useRef(new Map<string, GraphEdge>());
  const clusterByIdRef = useRef(new Map<string, ClusterSummary>());
  const clusterDragSnapshotRef = useRef<ClusterDragSnapshot | null>(null);
  const overlayDragRef = useRef<{
    clusterId: string;
    pointerId: number;
    lastX: number;
    lastY: number;
    snapshot: ClusterDragSnapshot | null;
  } | null>(null);
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
  const [clusterOverlays, setClusterOverlays] = useState<ClusterOverlay[]>([]);
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

  const selectedId =
    selected?.kind === "node"
      ? selected.node.id
      : selected?.kind === "edge"
        ? selected.key
        : selected?.kind === "cluster"
          ? selected.cluster.id
          : null;

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
    if (!containerRef.current || cyRef.current) return;

    const cy = cytoscape({
      container: containerRef.current,
      elements: [],
      maxZoom: 3,
      minZoom: 0.12,
      selectionType: "single",
      style: graphStyles(),
      wheelSensitivity: 0.16,
    });

    cy.autoungrabify(false);
    cy.userPanningEnabled(true);
    cy.userZoomingEnabled(true);

    cy.on("tap", "node[record]", (event: EventObject) => {
      const node = nodeByIdRef.current.get(event.target.id());
      if (node) setSelected({ kind: "node", node });
    });

    cy.on("tap", "node[cluster]", (event: EventObject) => {
      const cluster = clusterByIdRef.current.get(event.target.id());
      if (cluster) setSelected({ kind: "cluster", cluster });
    });

    cy.on("tap", "edge", (event: EventObject) => {
      const key = event.target.id();
      const edge = edgeByKeyRef.current.get(key);
      if (edge) setSelected({ kind: "edge", edge, key });
    });

    cy.on("tap", (event: EventObject) => {
      if (event.target === cy) setSelected(null);
    });

    const constrainDraggedRecord = (event: EventObject) => {
      if (!event.target?.id) return;
      keepRecordInsideCluster(cy, event.target.id(), clusterByIdRef.current);
      setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
    };
    cy.on("drag dragfree", "node[record]", constrainDraggedRecord);
    const syncClusterOverlays = () => {
      setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
    };
    const rememberClusterDragStart = (event: EventObject) => {
      if (!event.target?.id) return;
      clusterDragSnapshotRef.current = clusterDragSnapshot(cy, event.target.id());
    };
    const settleClusterDrag = (event: EventObject) => {
      if (!event.target?.id) return;
      if (clusterBoxesOverlap(cy, event.target.id(), clusterByIdRef.current)) {
        restoreClusterSnapshot(cy, clusterDragSnapshotRef.current);
      }
      clusterDragSnapshotRef.current = null;
      syncClusterOverlays();
    };
    cy.on("pan zoom layoutstop drag dragfree", "node[cluster]", syncClusterOverlays);
    cy.on("grab", "node[cluster]", rememberClusterDragStart);
    cy.on("dragfree", "node[cluster]", settleClusterDrag);
    cy.on("pan zoom", syncClusterOverlays);

    const observer = new ResizeObserver(() => {
      cy.resize();
      cy.fit(undefined, 58);
      syncClusterOverlays();
    });
    observer.observe(containerRef.current);
    cyRef.current = cy;

    return () => {
      cy.off("drag dragfree", "node[record]", constrainDraggedRecord);
      cy.off("pan zoom layoutstop drag dragfree", "node[cluster]", syncClusterOverlays);
      cy.off("grab", "node[cluster]", rememberClusterDragStart);
      cy.off("dragfree", "node[cluster]", settleClusterDrag);
      cy.off("pan zoom", syncClusterOverlays);
      observer.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, []);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || cy.destroyed()) return;

    cy.elements().remove();
    cy.add(graphElements(graph.nodes, graph.edges, clusters, nodeCluster));
    setClusterOverlays([]);
    if (clusterBy === "semantic" || clusterBy === "community") {
      runClusterBubbleLayout(cy, clusters);
    } else {
      runLayout(cy, edgeLength);
    }
    requestAnimationFrame(() => {
      setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
    });
  }, [clusterBy, clusters, edgeLength, graph.edges, graph.nodes, nodeCluster]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || cy.destroyed()) return;

    cy.elements().removeClass("faded focused");
    cy.elements().unselect();

    if (!selectedId) return;
    const element = cy.getElementById(selectedId);
    if (!element.length) return;

    let focus = element;
    if (selected?.kind === "node") {
      focus = element.closedNeighborhood().union(element.parent());
    } else if (selected?.kind === "edge") {
      focus = element.union(element.connectedNodes()).union(element.connectedNodes().parents());
    } else if (selected?.kind === "cluster") {
      focus = element.union(element.children()).union(element.children().connectedEdges());
    }

    cy.elements().difference(focus).addClass("faded");
    focus.addClass("focused");
    element.select();
  }, [selected, selectedId]);

  function fitGraph() {
    const cy = cyRef.current;
    if (!cy || cy.destroyed()) return;
    cy.fit(undefined, 58);
    setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
  }

  function relayoutGraph() {
    const cy = cyRef.current;
    if (!cy || cy.destroyed()) return;
    if (clusterBy === "semantic" || clusterBy === "community") {
      runClusterBubbleLayout(cy, clusters);
    } else {
      runLayout(cy, edgeLength);
    }
    requestAnimationFrame(() => {
      setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
    });
  }

  function beginOverlayClusterDrag(event: ReactPointerEvent<HTMLDivElement>, overlay: ClusterOverlay) {
    const cy = cyRef.current;
    if (!cy || cy.destroyed() || event.button !== 0) return;
    const cluster = clusterByIdRef.current.get(overlay.id);
    if (cluster) setSelected({ kind: "cluster", cluster });
    event.preventDefault();
    event.stopPropagation();
    event.currentTarget.setPointerCapture(event.pointerId);
    overlayDragRef.current = {
      clusterId: overlay.id,
      pointerId: event.pointerId,
      lastX: event.clientX,
      lastY: event.clientY,
      snapshot: clusterDragSnapshot(cy, overlay.id),
    };
  }

  function moveOverlayClusterDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = overlayDragRef.current;
    const cy = cyRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !cy || cy.destroyed()) return;
    event.preventDefault();
    event.stopPropagation();
    const deltaX = event.clientX - drag.lastX;
    const deltaY = event.clientY - drag.lastY;
    if (Math.abs(deltaX) < 0.5 && Math.abs(deltaY) < 0.5) return;
    moveClusterChildren(cy, drag.clusterId, deltaX / cy.zoom(), deltaY / cy.zoom());
    drag.lastX = event.clientX;
    drag.lastY = event.clientY;
    setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
  }

  function endOverlayClusterDrag(event: ReactPointerEvent<HTMLDivElement>) {
    const drag = overlayDragRef.current;
    const cy = cyRef.current;
    if (!drag || drag.pointerId !== event.pointerId || !cy || cy.destroyed()) return;
    event.preventDefault();
    event.stopPropagation();
    if (clusterBoxesOverlap(cy, drag.clusterId, clusterByIdRef.current)) {
      restoreClusterSnapshot(cy, drag.snapshot);
    }
    overlayDragRef.current = null;
    setClusterOverlays(measureClusterOverlays(cy, clusterByIdRef.current));
    if (event.currentTarget.hasPointerCapture(event.pointerId)) {
      event.currentTarget.releasePointerCapture(event.pointerId);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-[#050b16] text-slate-100">
      <div className="flex flex-col gap-3 border-b border-white/10 bg-[#07101f]/95 px-4 py-3 shadow-[0_1px_0_rgba(255,255,255,0.04)] backdrop-blur lg:flex-row lg:items-center lg:justify-between">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <h1 className="text-sm font-semibold text-white">Graph</h1>
            <span className="rounded-full border border-sky-400/20 bg-sky-400/10 px-2 py-0.5 text-[11px] font-medium text-sky-200">
              {clusterBy === "semantic" ? "Topic view" : `${CLUSTER_OPTIONS.find((option) => option.value === clusterBy)?.label} view`}
            </span>
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <Metric label="nodes" value={graph.nodes.length} />
            <Metric label="edges" value={graph.edges.length} />
            <Metric label="clusters" value={clusters.length} />
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
                onClick={() => setClusterBy(option.value)}
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
              min={110}
              max={320}
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
        <div className="relative min-h-[520px] min-w-0 overflow-hidden bg-[radial-gradient(circle_at_18%_12%,rgba(59,130,246,0.2),transparent_30%),radial-gradient(circle_at_78%_24%,rgba(45,212,191,0.14),transparent_28%),radial-gradient(circle_at_46%_82%,rgba(168,85,247,0.1),transparent_34%),#020617]">
          <div className="pointer-events-none absolute inset-0 z-0 bg-[linear-gradient(rgba(148,163,184,0.05)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.05)_1px,transparent_1px)] bg-[size:34px_34px]" />
          <div className="pointer-events-none absolute inset-0 z-[1] bg-[radial-gradient(circle_at_center,transparent,rgba(2,6,23,0.68))]" />
          <div className="pointer-events-none absolute inset-0 z-[4] overflow-hidden">
            {clusterOverlays.map((overlay) => {
              const selectedCluster = selected?.kind === "cluster" && selected.cluster.id === overlay.id;
              return (
                <div
                  key={overlay.id}
                  className={`absolute border transition-opacity duration-150 ${
                    selectedCluster ? "opacity-100" : selected ? "opacity-35" : "opacity-78"
                  }`}
                  style={{
                    background: `radial-gradient(circle at 50% 44%, ${alphaColor(overlay.color, selectedCluster ? 0.2 : 0.13)} 0%, ${alphaColor(overlay.color, selectedCluster ? 0.1 : 0.06)} 58%, transparent 100%)`,
                    borderColor: alphaColor(overlay.color, selectedCluster ? 0.72 : 0.34),
                    borderRadius: "44% 56% 51% 49% / 55% 43% 57% 45%",
                    boxShadow: `0 0 ${selectedCluster ? 46 : 28}px ${alphaColor(overlay.color, selectedCluster ? 0.18 : 0.08)}`,
                    height: overlay.height,
                    left: overlay.x,
                    top: overlay.y,
                    width: overlay.width,
                  }}
                >
                  <div
                    className="pointer-events-auto absolute left-1/2 top-2 max-w-[78%] -translate-x-1/2 cursor-grab select-none rounded-full border px-2.5 py-1 text-center text-[10px] font-semibold leading-3 text-white shadow-[0_8px_24px_rgba(2,6,23,0.28)] backdrop-blur active:cursor-grabbing"
                    onPointerDown={(event) => beginOverlayClusterDrag(event, overlay)}
                    onPointerMove={moveOverlayClusterDrag}
                    onPointerUp={endOverlayClusterDrag}
                    onPointerCancel={endOverlayClusterDrag}
                    style={{
                      backgroundColor: alphaColor("#020617", 0.74),
                      borderColor: alphaColor(overlay.color, 0.42),
                    }}
                  >
                    <span className="line-clamp-2">{overlay.label}</span>
                    <span className="mt-0.5 block text-[9px] font-medium text-slate-400">
                      {overlay.count} records
                    </span>
                  </div>
                </div>
              );
            })}
          </div>
          <div className="absolute inset-0 z-[3]">
            <div ref={containerRef} className="h-full w-full" />
          </div>

          {loading && (
            <div className="absolute left-4 top-4 rounded-md border border-white/10 bg-slate-950/85 px-3 py-2 text-sm text-slate-200 shadow-sm backdrop-blur">
              Loading graph...
            </div>
          )}

          {error && (
            <div className="absolute left-4 right-4 top-4 rounded-md border border-red-500/20 bg-red-50 px-3 py-2 text-sm text-red-700 shadow-sm">
              {error}
            </div>
          )}

          {!loading && !error && graph.nodes.length === 0 && (
            <div className="absolute inset-0 flex items-center justify-center p-8 text-center">
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
  onOpenRecord,
}: {
  selection: Selection;
  nodeById: Map<string, GraphNode>;
  onOpenRecord?: (recordId: string) => void;
}) {
  if (!selection) {
    return (
      <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <div className="mb-3 h-2 w-16 rounded-full bg-gradient-to-r from-sky-400 to-teal-300 shadow-[0_0_22px_rgba(56,189,248,0.35)]" />
        <h2 className="text-sm font-semibold text-white">Inspect the graph</h2>
        <p className="mt-2 text-sm leading-6 text-slate-400">
          Click a cluster cloud to understand a topic. Click a record node to read
          the memory. Click a relationship to inspect why two records are linked.
        </p>
        <div className="mt-4 grid grid-cols-3 gap-2 text-center text-[11px] text-slate-400">
          <span className="rounded-lg border border-white/10 bg-white/[0.035] px-2 py-2">clusters</span>
          <span className="rounded-lg border border-white/10 bg-white/[0.035] px-2 py-2">records</span>
          <span className="rounded-lg border border-white/10 bg-white/[0.035] px-2 py-2">links</span>
        </div>
      </div>
    );
  }

  if (selection.kind === "cluster") {
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
          <div
            className="mb-3 h-2 w-20 rounded-full shadow-[0_0_22px_currentColor]"
            style={{ backgroundColor: selection.cluster.color }}
          />
          <h2 className="text-base font-semibold leading-6 text-white">
            {selection.cluster.label}
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            {selection.cluster.lensLabel} · {selection.cluster.nodes.length} nodes · {selection.cluster.edges.length} connected edges
          </p>
        </div>

        <div className="rounded-2xl border border-white/10 bg-white/[0.045] p-4">
          <div className="text-xs font-medium uppercase tracking-wide text-slate-500">
            Cluster summary
          </div>
          <p className="mt-2 text-sm leading-6 text-slate-200">
            {selection.cluster.description}
          </p>
        </div>

        <div className="grid grid-cols-2 gap-3 text-xs">
          <Detail
            label="Main types"
            value={selection.cluster.topKinds.join(", ") || "Unknown"}
          />
          <Detail
            label="Projects"
            value={selection.cluster.topProjects.join(", ") || "Unscoped"}
          />
        </div>

        {selection.cluster.representativeTitles.length > 0 && (
          <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              Representative records
            </div>
            <ul className="space-y-1.5 text-sm leading-5 text-slate-300">
              {selection.cluster.representativeTitles.map((title) => (
                <li key={title} className="rounded-lg bg-white/[0.045] px-3 py-2">
                  {title}
                </li>
              ))}
            </ul>
          </div>
        )}

        <div className="space-y-2">
          {selection.cluster.nodes.slice(0, 20).map((node) => (
            <button
              key={node.id}
              type="button"
              onClick={() => onOpenRecord?.(node.id)}
              className="block w-full rounded-xl border border-white/10 bg-white/[0.035] p-3 text-left transition hover:border-sky-400/70 hover:bg-white/[0.07]"
            >
              <div className="text-sm font-medium text-white">{node.label || "Untitled"}</div>
              <div className="mt-1 text-xs text-slate-400">
                {formatRecordKind(node.record_kind)} · {formatScopeLabel(node.project)}
              </div>
            </button>
          ))}
        </div>
      </div>
    );
  }

  if (selection.kind === "node") {
    const node = selection.node;
    return (
      <div className="space-y-4">
        <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
          <div className="mb-2 flex flex-wrap gap-2">
            <Badge>{formatRecordKind(node.record_kind)}</Badge>
            <Badge>{node.status || "unknown"}</Badge>
          </div>
          <h2 className="text-base font-semibold leading-6 text-white">
            {node.label || "Untitled record"}
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            {formatScopeLabel(node.project)} · updated {compactDate(node.updated_at)}
          </p>
        </div>

        {(node.summary || node.body) && (
          <p className="whitespace-pre-wrap rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-sm leading-6 text-slate-300">
            {truncate(node.summary || node.body)}
          </p>
        )}

        {node.tags && node.tags.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {node.tags.map((tag) => (
              <span
                key={tag}
                className="rounded border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-300"
              >
                {tag}
              </span>
            ))}
          </div>
        )}

        <dl className="grid grid-cols-2 gap-3 text-xs">
          <Detail label="Confidence" value={node.confidence == null ? "n/a" : `${Math.round(node.confidence * 100)}%`} />
          <Detail label="Created" value={compactDate(node.created_at)} />
          <Detail label="Topic" value={formatScopeLabel(node.semantic_cluster)} />
          <Detail label="Community" value={formatScopeLabel(node.community_cluster)} />
        </dl>

        {onOpenRecord && (
          <button
            type="button"
            onClick={() => onOpenRecord(node.id)}
            className="h-10 w-full rounded-full bg-sky-400 px-4 text-sm font-semibold text-slate-950 shadow-[0_0_24px_rgba(56,189,248,0.25)] transition hover:bg-sky-300"
          >
            Open full record
          </button>
        )}
      </div>
    );
  }

  const source = nodeById.get(selection.edge.source);
  const target = nodeById.get(selection.edge.target);

  return (
    <div className="space-y-4">
      <div className="rounded-2xl border border-white/10 bg-white/[0.035] p-4">
        <Badge>{humanizeToken(selection.edge.kind)}</Badge>
        <h2 className="mt-2 text-base font-semibold leading-6 text-white">
          {relationshipLabel(selection.edge)}
        </h2>
        <p className="mt-1 text-xs text-slate-400">
          {source?.label || selection.edge.source} → {target?.label || selection.edge.target}
        </p>
      </div>

      {selection.edge.rationale && (
        <p className="whitespace-pre-wrap rounded-2xl border border-white/10 bg-white/[0.035] p-4 text-sm leading-6 text-slate-300">
          {selection.edge.rationale}
        </p>
      )}

      <dl className="grid grid-cols-2 gap-3 text-xs">
        <Detail label="Weight" value={String(selection.edge.weight ?? "n/a")} />
        <Detail label="Status" value={selection.edge.status || "unknown"} />
        <Detail label="Evidence" value={String(selection.edge.evidence_record_ids?.length || 0)} />
        <Detail label="Edge ID" value={selection.key.slice(0, 12)} />
      </dl>
    </div>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span className="rounded-full border border-white/10 bg-white/[0.055] px-2 py-1 text-xs font-medium text-slate-300">
      {children}
    </span>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-xl border border-white/10 bg-white/[0.035] p-3">
      <dt className="text-slate-500">{label}</dt>
      <dd className="mt-1 truncate font-medium text-slate-100" title={value}>
        {value}
      </dd>
    </div>
  );
}
