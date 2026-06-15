import React, { useMemo, useRef, useEffect, useState } from "react";

export interface CitationNode {
  id: string;
  title: string;
  year?: number | string;
  venue?: string;
  citations?: number;
  external?: boolean;
}

export interface CitationEdge {
  source: string;
  target: string;
  type?: string;
}

interface LayoutNode {
  x: number;
  y: number;
  width: number;
  height: number;
  data: CitationNode;
}

interface LayoutEdge {
  sourceNode: LayoutNode;
  targetNode: LayoutNode;
  data: CitationEdge;
}

// ── Force-directed layout (simplified) ──

function forceLayout(
  nodes: CitationNode[],
  edges: CitationEdge[],
  width: number,
  height: number,
): { layoutNodes: LayoutNode[]; layoutEdges: LayoutEdge[] } {
  const nodeMap = new Map<string, CitationNode>();
  for (const n of nodes) nodeMap.set(n.id, n);

  // Only include nodes that have edges (visible graph)
  const visibleIds = new Set<string>();
  for (const e of edges) {
    visibleIds.add(e.source);
    visibleIds.add(e.target);
  }
  // Also include isolated nodes if they have citations
  for (const n of nodes) {
    if (!visibleIds.has(n.id) && (n.citations || 0) > 0) {
      visibleIds.add(n.id);
    }
  }

  const visibleNodes = nodes.filter((n) => visibleIds.has(n.id));
  const visibleEdges = edges.filter(
    (e) => visibleIds.has(e.source) && visibleIds.has(e.target),
  );

  if (visibleNodes.length === 0) {
    return { layoutNodes: [], layoutEdges: [] };
  }

  // Simple layered layout: sources on left, targets on right
  // First pass: assign layers by topologically sorting
  const inDegree = new Map<string, number>();
  const outEdges = new Map<string, string[]>();
  for (const n of visibleNodes) {
    inDegree.set(n.id, 0);
    outEdges.set(n.id, []);
  }
  for (const e of visibleEdges) {
    inDegree.set(e.target, (inDegree.get(e.target) || 0) + 1);
    outEdges.get(e.source)?.push(e.target);
  }

  // Topological sort with BFS
  const layers = new Map<string, number>();
  const queue: string[] = [];
  for (const [id, deg] of inDegree) {
    if (deg === 0) {
      queue.push(id);
      layers.set(id, 0);
    }
  }

  let maxLayer = 0;
  while (queue.length > 0) {
    const current = queue.shift()!;
    const currentLayer = layers.get(current) || 0;
    for (const target of outEdges.get(current) || []) {
      const nextLayer = currentLayer + 1;
      if (!layers.has(target) || (layers.get(target) || 0) < nextLayer) {
        layers.set(target, nextLayer);
        if (nextLayer > maxLayer) maxLayer = nextLayer;
      }
      const newDeg = (inDegree.get(target) || 1) - 1;
      if (newDeg <= 0) {
        queue.push(target);
      }
    }
  }

  // Assign all unassigned nodes to layer 0
  for (const n of visibleNodes) {
    if (!layers.has(n.id)) layers.set(n.id, 0);
  }

  // Position nodes per layer
  const layerGroups = new Map<number, CitationNode[]>();
  for (const n of visibleNodes) {
    const l = layers.get(n.id) || 0;
    if (!layerGroups.has(l)) layerGroups.set(l, []);
    layerGroups.get(l)!.push(n);
  }

  const padding = 20;
  const nodeWidth = 180;
  const nodeHeight = 60;
  const layerSpacing = (width - padding * 2) / Math.max(maxLayer + 1, 1);
  const layoutNodes: LayoutNode[] = [];

  for (const [layer, group] of layerGroups) {
    const spacing = (height - padding * 2) / Math.max(group.length + 1, 1);
    const x = padding + layer * layerSpacing + layerSpacing / 2 - nodeWidth / 2;
    group.forEach((n, i) => {
      layoutNodes.push({
        x,
        y: padding + (i + 1) * spacing - nodeHeight / 2,
        width: nodeWidth,
        height: nodeHeight,
        data: n,
      });
    });
  }

  // Build edge references
  const nodePosMap = new Map<string, LayoutNode>();
  for (const ln of layoutNodes) nodePosMap.set(ln.data.id, ln);

  const layoutEdges: LayoutEdge[] = [];
  for (const e of visibleEdges) {
    const sn = nodePosMap.get(e.source);
    const tn = nodePosMap.get(e.target);
    if (sn && tn) {
      layoutEdges.push({ sourceNode: sn, targetNode: tn, data: e });
    }
  }

  return { layoutNodes, layoutEdges };
}

// ── Color palette per layer ──

const LAYER_COLORS = [
  { bg: "#3B82F6", text: "#fff" },   // blue
  { bg: "#8B5CF6", text: "#fff" },   // violet
  { bg: "#EC4899", text: "#fff" },   // pink
  { bg: "#F59E0B", text: "#fff" },   // amber
  { bg: "#10B981", text: "#fff" },   // emerald
  { bg: "#6366F1", text: "#fff" },   // indigo
  { bg: "#EF4444", text: "#fff" },   // red
  { bg: "#14B8A6", text: "#fff" },   // teal
];

function getColor(index: number) {
  return LAYER_COLORS[index % LAYER_COLORS.length];
}

// ── React Component ──

export function CitationGraph({
  nodes,
  edges,
  width = 900,
  height = 400,
}: {
  nodes: CitationNode[];
  edges: CitationEdge[];
  width?: number;
  height?: number;
}) {
  const { layoutNodes, layoutEdges } = useMemo(
    () => forceLayout(nodes, edges, width, height),
    [nodes, edges, width, height],
  );

  if (layoutNodes.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] p-8 text-sm text-[var(--text-muted)]">
        No citation data to display
      </div>
    );
  }

  // Compute layer index per node
  const layerMap = new Map<string, number>();
  layoutNodes.forEach((ln, i) => {
    if (!layerMap.has(ln.data.id)) {
      // Approximate layer by x position
      const layer = Math.round((ln.x / width) * 8);
      layerMap.set(ln.data.id, i);
    }
  });

  // SVG arrows marker
  const markerId = "citation-arrow";

  return (
    <div className="my-3 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--bg)] p-4">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ minHeight: `${Math.min(height, 500)}px` }}
      >
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 10 10"
            refX="8"
            refY="5"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-muted)" opacity="0.6" />
          </marker>
        </defs>

        {/* Edges */}
        {layoutEdges.map((edge, i) => {
          const sx = edge.sourceNode.x + edge.sourceNode.width;
          const sy = edge.sourceNode.y + edge.sourceNode.height / 2;
          const tx = edge.targetNode.x;
          const ty = edge.targetNode.y + edge.targetNode.height / 2;
          const midX = (sx + tx) / 2;

          return (
            <g key={`edge-${i}`}>
              {/* Curved path */}
              <path
                d={`M ${sx} ${sy} C ${midX} ${sy}, ${midX} ${ty}, ${tx} ${ty}`}
                fill="none"
                stroke="var(--text-muted)"
                strokeWidth="1.5"
                opacity="0.5"
                markerEnd={`url(#${markerId})`}
              />
            </g>
          );
        })}

        {/* Nodes */}
        {layoutNodes.map((ln, i) => {
          const color = getColor(i);
          const title = ln.data.title.length > 28
            ? ln.data.title.slice(0, 26) + "..."
            : ln.data.title;
          const year = ln.data.year || "";
          const venue = ln.data.venue || "";
          const citations = ln.data.citations || 0;

          return (
            <g key={`node-${i}`}>
              <rect
                x={ln.x}
                y={ln.y}
                width={ln.width}
                height={ln.height}
                rx="8"
                ry="8"
                fill={color.bg}
                opacity="0.9"
                stroke="rgba(255,255,255,0.15)"
                strokeWidth="1"
              />
              <text
                x={ln.x + ln.width / 2}
                y={ln.y + 20}
                textAnchor="middle"
                fill={color.text}
                fontSize="11"
                fontWeight="600"
              >
                {title}
              </text>
              <text
                x={ln.x + ln.width / 2}
                y={ln.y + 38}
                textAnchor="middle"
                fill={color.text}
                fontSize="10"
                opacity="0.85"
              >
                {year ? `${year}` : ""}{year && venue ? " | " : ""}{venue ? `${venue}` : ""}
              </text>
              {citations > 0 && (
                <text
                  x={ln.x + ln.width - 8}
                  y={ln.y + 14}
                  textAnchor="end"
                  fill={color.text}
                  fontSize="9"
                  opacity="0.7"
                >
                  {citations} cit.
                </text>
              )}
            </g>
          );
        })}
      </svg>
    </div>
  );
}

// ── Mermaid parser: extracts nodes & edges from a citation-style mermaid string ──

export function parseCitationMermaid(mermaidStr: string): {
  nodes: CitationNode[];
  edges: CitationEdge[];
} {
  const nodes: CitationNode[] = [];
  const edges: CitationEdge[] = [];

  // Extract node IDs from the mermaid graph
  // Pattern: [id[label<br/>year<br/>venue]]  or  (id[label<br/>year])
  const nodeRegex = /^\s+([\w\/\.\-]+)[\[\(].*?[\]]/gm;
  const edgeRegex = /^\s+([\w\/\.\-]+)\s*-->.*?\|cites\|\s*([\w\/\.\-]+)/gm;
  const squareNodeRegex = /^\s+([\w\/\.\-]+)\[\[(.+?)\]\]/gm;

  // Also extract simple Mermaid TB node definitions
  const allLines = mermaidStr.split("\n");
  for (const rawLine of allLines) {
    const line = rawLine.trim();

    // Skip graph directives, subgraphs, comments
    if (line.startsWith("graph ") || line.startsWith("subgraph ") || line.startsWith("end") || line.startsWith("%%") || !line) continue;

    // Node definitions: [id[label]]
    const nodeMatch = line.match(/^([\w\/\.\-_]+)\[\[\s*(.+?)\s*\]\]/);
    if (nodeMatch) {
      const id = nodeMatch[1];
      let label = nodeMatch[2].trim();
      // Parse year from label end
      const yearMatch = label.match(/(\d{4})$/);
      const year = yearMatch ? parseInt(yearMatch[1]) : undefined;
      if (year) label = label.replace(/\s*\d{4}\s*$/, "").trim();
      nodes.push({ id, title: label, year, external: false });
      continue;
    }

    // Edge definitions: A --> B, A -->|cites| B, A -- cites --> B
    const edgeMatch = line.match(/^([\w\/\.\-_]+)\s*--(?:[^>-]*)?--?>\s*(?:\|[^|]*\|\s*)?([\w\/\.\-_]+)/);
    if (edgeMatch) {
      const source = edgeMatch[1];
      const targetStr = edgeMatch[2].trim();
      if (targetStr) {
        edges.push({ source, target: targetStr, type: "cites" });
      }
    }
  }

  return { nodes, edges };
}

// ── Convenience: render from JSON ──

export function CitationGraphFromJson({
  data,
  width,
  height,
}: {
  data: { nodes?: CitationNode[]; edges?: CitationEdge[] };
  width?: number;
  height?: number;
}) {
  const nodes = data?.nodes || [];
  const edges = data?.edges || [];
  return <CitationGraph nodes={nodes} edges={edges} width={width} height={height} />;
}
