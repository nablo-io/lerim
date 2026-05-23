"use client";

import { useEffect, useMemo, useRef, useState } from "react";
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
  description: string;
  representativeTitles: string[];
  topKinds: string[];
  topProjects: string[];
  color: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
};

const DEFAULT_MAX_NODES = 140;
const DEFAULT_EDGE_LENGTH = 180;

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

  const summarizedClusters = Array.from(clusters.values()).map((cluster) => ({
    ...cluster,
    description: clusterDescription(cluster.nodes),
    representativeTitles: representativeTitles(cluster.nodes),
    topKinds: rankedValues(cluster.nodes.map((node) => node.record_kind), 3).map(formatRecordKind),
    topProjects: rankedValues(cluster.nodes.map((node) => node.project), 3).map(formatScopeLabel),
  }));

  return { clusters: summarizedClusters, nodeCluster };
}

function graphStyles(): StylesheetJson {
  return [
    {
      selector: "node[cluster]",
      style: {
        "background-color": "data(color)",
        "background-opacity": 0.08,
        "border-color": "data(color)",
        "border-opacity": 0.42,
        "border-style": "dashed",
        "border-width": 2,
        color: "#dbeafe",
        "compound-sizing-wrt-labels": "include",
        "font-size": 12,
        "font-weight": 800,
        label: "data(label)",
        padding: 28,
        shape: "round-rectangle",
        "text-background-color": "#020617",
        "text-background-opacity": 0.72,
        "text-background-padding": 4,
        "text-halign": "center",
        "text-margin-y": -8,
        "text-max-width": 210,
        "text-valign": "top",
        "text-wrap": "wrap",
      },
    },
    {
      selector: "node[record]",
      style: {
        "background-color": "data(color)",
        "border-color": "#0f172a",
        "border-opacity": 0.95,
        "border-width": 2.5,
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
        "border-width": 4,
        "font-size": 11,
        "text-background-opacity": 0.92,
        "text-max-width": 160,
      },
    },
    {
      selector: "edge",
      style: {
        "curve-style": "bezier",
        "font-size": 9.5,
        "font-weight": 700,
        label: "data(label)",
        "line-color": "data(color)",
        opacity: 0.86,
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
      label: `${cluster.label} (${cluster.nodes.length})\n${shortLabel(cluster.description, 86)}`,
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

function runLayout(cy: Core | null, edgeLength: number) {
  if (!cy || cy.destroyed()) return;
  cy.layout({
    name: "fcose",
    animate: true,
    animationDuration: 420,
    edgeElasticity: 0.35,
    fit: true,
    gravity: 0.55,
    idealEdgeLength: edgeLength,
    nestingFactor: 0.55,
    nodeRepulsion: 7000,
    nodeSeparation: 88,
    numIter: 2500,
    packComponents: true,
    padding: 58,
    quality: "default",
    randomize: true,
    tilingPaddingHorizontal: 34,
    tilingPaddingVertical: 34,
  } as cytoscape.LayoutOptions).run();
}

export default function GraphExplorer({ onRecordClick }: GraphExplorerProps) {
  const cyRef = useRef<Core | null>(null);
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

    const observer = new ResizeObserver(() => {
      cy.resize();
      cy.fit(undefined, 58);
    });
    observer.observe(containerRef.current);
    cyRef.current = cy;

    return () => {
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
    runLayout(cy, edgeLength);
  }, [clusters, edgeLength, graph.edges, graph.nodes, nodeCluster]);

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
  }

  function relayoutGraph() {
    runLayout(cyRef.current, edgeLength);
  }

  return (
    <div className="flex h-full min-h-0 flex-col bg-slate-950 text-slate-100">
      <div className="flex flex-col gap-3 border-b border-white/10 bg-slate-950/95 px-4 py-3 shadow-[0_1px_0_rgba(255,255,255,0.04)] lg:flex-row lg:items-center lg:justify-between">
        <div>
          <h1 className="text-sm font-semibold text-white">Context Graph</h1>
          <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-slate-400">
            <Metric label="nodes" value={graph.nodes.length} />
            <Metric label="edges" value={graph.edges.length} />
            <Metric label="clusters" value={clusters.length} />
            <Metric label="records" value={graph.totalRecords} />
            {graph.truncated && <span className="text-amber-700">showing first {graph.nodes.length}</span>}
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <label className="flex h-9 items-center gap-2 rounded-md border border-white/10 bg-white/5 px-2 text-xs text-slate-300 shadow-sm">
            Cluster
            <select
              value={clusterBy}
              onChange={(event) => setClusterBy(event.target.value as ClusterBy)}
              className="bg-slate-950 text-sm text-white outline-none"
            >
              <option value="semantic">Topic</option>
              <option value="community">Community</option>
              <option value="project">Project</option>
              <option value="type">Type</option>
            </select>
          </label>
          <label className="flex h-9 items-center gap-2 rounded-md border border-white/10 bg-white/5 px-2 text-xs text-slate-300 shadow-sm">
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
          <label className="flex h-9 items-center gap-2 rounded-md border border-white/10 bg-white/5 px-2 text-xs text-slate-300 shadow-sm">
            Edge length
            <input
              type="range"
              min={110}
              max={320}
              value={edgeLength}
              onChange={(event) => setEdgeLength(Number(event.target.value))}
              className="w-24"
            />
          </label>
          <button type="button" onClick={loadGraph} className="rounded-md border border-white/10 bg-white/8 px-3 py-2 text-sm font-medium text-slate-100 shadow-sm transition hover:bg-white/12 disabled:opacity-60" disabled={loading}>
            {loading ? "Loading" : "Refresh"}
          </button>
          <button type="button" onClick={fitGraph} className="rounded-md border border-white/10 bg-white/8 px-3 py-2 text-sm font-medium text-slate-100 shadow-sm transition hover:bg-white/12">
            Fit
          </button>
          <button type="button" onClick={relayoutGraph} className="rounded-md border border-white/10 bg-white/8 px-3 py-2 text-sm font-medium text-slate-100 shadow-sm transition hover:bg-white/12">
            Layout
          </button>
        </div>
      </div>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
        <div className="relative min-h-[520px] min-w-0 overflow-hidden bg-[radial-gradient(circle_at_20%_15%,rgba(59,130,246,0.16),transparent_28%),radial-gradient(circle_at_78%_28%,rgba(45,212,191,0.12),transparent_26%),#020617]">
          <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(rgba(148,163,184,0.055)_1px,transparent_1px),linear-gradient(90deg,rgba(148,163,184,0.055)_1px,transparent_1px)] bg-[size:32px_32px]" />
          <div className="absolute inset-0">
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

        <aside className="min-h-0 overflow-y-auto border-t border-white/10 bg-slate-950 p-4 lg:border-l lg:border-t-0">
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
    <span>
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
      <div>
        <h2 className="text-sm font-semibold text-white">Click something</h2>
        <p className="mt-2 text-sm leading-6 text-slate-400">
          Click a node to read its content. Click an edge to inspect the relationship.
          Click a cluster cloud to see what it contains.
        </p>
      </div>
    );
  }

  if (selection.kind === "cluster") {
    return (
      <div className="space-y-4">
        <div>
          <div
            className="mb-2 h-2 w-16 rounded-full shadow-[0_0_18px_currentColor]"
            style={{ backgroundColor: selection.cluster.color }}
          />
          <h2 className="text-base font-semibold leading-6 text-white">
            {selection.cluster.label}
          </h2>
          <p className="mt-1 text-xs text-slate-400">
            {selection.cluster.nodes.length} nodes · {selection.cluster.edges.length} connected edges
          </p>
        </div>

        <div className="rounded-md border border-white/10 bg-white/5 p-3">
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
          <div>
            <div className="mb-2 text-xs font-medium uppercase tracking-wide text-slate-500">
              Representative records
            </div>
            <ul className="space-y-1.5 text-sm leading-5 text-slate-300">
              {selection.cluster.representativeTitles.map((title) => (
                <li key={title} className="rounded-md bg-white/5 px-2 py-1">
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
              className="block w-full rounded-md border border-white/10 bg-white/5 p-3 text-left transition hover:border-sky-400/70 hover:bg-white/8"
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
        <div>
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
          <p className="whitespace-pre-wrap text-sm leading-6 text-slate-300">
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
            className="h-10 w-full rounded-md bg-sky-500 px-4 text-sm font-medium text-white transition hover:bg-sky-400"
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
      <div>
        <Badge>{humanizeToken(selection.edge.kind)}</Badge>
        <h2 className="mt-2 text-base font-semibold leading-6 text-white">
          {relationshipLabel(selection.edge)}
        </h2>
        <p className="mt-1 text-xs text-slate-400">
          {source?.label || selection.edge.source} → {target?.label || selection.edge.target}
        </p>
      </div>

      {selection.edge.rationale && (
        <p className="whitespace-pre-wrap text-sm leading-6 text-slate-300">
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
    <span className="rounded-full border border-white/10 bg-white/5 px-2 py-1 text-xs font-medium text-slate-300">
      {children}
    </span>
  );
}

function Detail({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-md border border-white/10 bg-white/5 p-3">
      <dt className="text-slate-500">{label}</dt>
      <dd className="mt-1 truncate font-medium text-slate-100" title={value}>
        {value}
      </dd>
    </div>
  );
}
