import React, { useMemo } from "react";
import type { MemoryGraphNode, MemoryGraphEdge } from "../../types.ts";

// ── Color palette by node kind ──

const KIND_COLORS: Record<string, { bg: string; text: string; border: string }> = {
  skill:       { bg: "#3B82F6", text: "#fff", border: "#2563EB" },   // blue
  tool:        { bg: "#10B981", text: "#fff", border: "#059669" },   // green
  paper:       { bg: "#F59E0B", text: "#000", border: "#D97706" },   // amber
  author:      { bg: "#8B5CF6", text: "#fff", border: "#7C3AED" },   // violet
  venue:       { bg: "#EC4899", text: "#fff", border: "#DB2777" },   // pink
  decision:    { bg: "#EF4444", text: "#fff", border: "#DC2626" },   // red
  alternative: { bg: "#F97316", text: "#fff", border: "#EA580C" },   // orange
  option:      { bg: "#F97316", text: "#fff", border: "#EA580C" },   // orange (same as alternative)
  topic:       { bg: "#6366F1", text: "#fff", border: "#4F46E5" },   // indigo
  claim:       { bg: "#14B8A6", text: "#fff", border: "#0D9488" },   // teal
  evidence:    { bg: "#84CC16", text: "#000", border: "#65A30D" },   // lime
  artifact:    { bg: "#EAB308", text: "#000", border: "#CA8A04" },   // yellow
  project:     { bg: "#06B6D4", text: "#fff", border: "#0891B2" },   // cyan
  session:     { bg: "#A855F7", text: "#fff", border: "#9333EA" },   // purple
  owner:       { bg: "#78716C", text: "#fff", border: "#57534E" },   // warm gray
  memory:      { bg: "#9CA3AF", text: "#fff", border: "#6B7280" },   // gray
  entity:      { bg: "#FB923C", text: "#000", border: "#F97316" },   // orange light
  document:    { bg: "#A3E635", text: "#000", border: "#84CC16" },   // lime light
  community:   { bg: "#C084FC", text: "#fff", border: "#A855F7" },   // violet light
  rag_entity:  { bg: "#FDBA74", text: "#000", border: "#FB923C" },   // orange lighter
  literature_review: { bg: "#F472B6", text: "#fff", border: "#EC4899" }, // pink light
  memory_card: { bg: "#D1D5DB", text: "#000", border: "#9CA3AF" },   // gray light
  scope:       { bg: "#94A3B8", text: "#fff", border: "#64748B" },   // slate
  hyperedge:   { bg: "#475569", text: "#fff", border: "#334155" },   // slate dark
};

const DEFAULT_COLOR = { bg: "#CBD5E1", text: "#000", border: "#94A3B8" };

function kindColor(kind: string | undefined) {
  return KIND_COLORS[kind || ""] || DEFAULT_COLOR;
}

// ── Layout types ──

interface LayoutNode {
  x: number; y: number;
  width: number; height: number;
  data: MemoryGraphNode;
}

interface LayoutEdge {
  sourceNode: LayoutNode;
  targetNode: LayoutNode;
  data: MemoryGraphEdge;
}

// ── Force-directed layout (simplified: layered by kind → left-to-right) ──

function layeredLayout(
  nodes: MemoryGraphNode[],
  edges: MemoryGraphEdge[],
  width: number,
  height: number,
): { layoutNodes: LayoutNode[]; layoutEdges: LayoutEdge[] } {
  const nodeMap = new Map<string, MemoryGraphNode>();
  for (const n of nodes) nodeMap.set(n.id, n);

  // Group nodes by kind
  const kindOrder: string[] = [];
  const kindNodes = new Map<string, MemoryGraphNode[]>();
  for (const n of nodes) {
    const k = n.kind || n.type || "other";
    if (!kindNodes.has(k)) {
      kindNodes.set(k, []);
      kindOrder.push(k);
    }
    kindNodes.get(k)!.push(n);
  }

  const padding = 40;
  const nodeWidth = 170;
  const nodeHeight = 56;
  const layerCount = kindOrder.length || 1;
  const layerSpacing = Math.min((width - padding * 2) / layerCount, 220);

  const layoutNodes: LayoutNode[] = [];
  const posMap = new Map<string, LayoutNode>();

  kindOrder.forEach((kind, layerIdx) => {
    const group = kindNodes.get(kind) || [];
    const x = padding + layerIdx * layerSpacing;
    const spacing = Math.min((height - padding * 2) / Math.max(group.length + 1, 1), 100);
    group.forEach((n, i) => {
      const ln: LayoutNode = {
        x,
        y: padding + (i + 1) * spacing - nodeHeight / 2,
        width: nodeWidth,
        height: nodeHeight,
        data: n,
      };
      layoutNodes.push(ln);
      posMap.set(n.id, ln);
    });
  });

  const layoutEdges: LayoutEdge[] = [];
  for (const e of edges) {
    const sn = posMap.get(e.source);
    const tn = posMap.get(e.target);
    if (sn && tn) {
      layoutEdges.push({ sourceNode: sn, targetNode: tn, data: e });
    }
  }

  return { layoutNodes, layoutEdges };
}

// ── SVG Component ──

export function OntologyGraph({
  nodes,
  edges,
  width = 1000,
  height = 500,
}: {
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  width?: number;
  height?: number;
}) {
  const { layoutNodes, layoutEdges } = useMemo(
    () => layeredLayout(nodes, edges, width, height),
    [nodes, edges, width, height],
  );

  if (layoutNodes.length === 0) {
    return (
      <div className="flex items-center justify-center rounded-lg border border-dashed border-[var(--border-strong)] bg-[var(--bg-soft)] p-8 text-sm text-[var(--text-muted)]">
        No ontology data to display
      </div>
    );
  }

  const markerId = "onto-arrow";

  return (
    <div className="my-3 overflow-auto rounded-lg border border-[var(--border)] bg-[var(--bg)] p-4">
      <svg
        viewBox={`0 0 ${width} ${height}`}
        className="w-full"
        style={{ minHeight: `${Math.min(height, 600)}px` }}
      >
        <defs>
          <marker
            id={markerId}
            viewBox="0 0 10 10"
            refX="8" refY="5"
            markerWidth="6" markerHeight="6"
            orient="auto"
          >
            <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-muted)" opacity="0.5" />
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
              <path
                d={`M ${sx} ${sy} C ${midX} ${sy}, ${midX} ${ty}, ${tx} ${ty}`}
                fill="none"
                stroke="var(--text-muted)"
                strokeWidth="1.5"
                opacity="0.4"
                markerEnd={`url(#${markerId})`}
              />
              {edge.data.label && (
                <text
                  x={midX}
                  y={(sy + ty) / 2 - 6}
                  textAnchor="middle"
                  fill="var(--text-muted)"
                  fontSize="9"
                  opacity="0.7"
                >
                  {edge.data.label}
                </text>
              )}
            </g>
          );
        })}

        {/* Nodes */}
        {layoutNodes.map((ln, i) => {
          const kind = ln.data.kind || ln.data.type || "other";
          const color = kindColor(kind);
          const label = (ln.data.label || ln.data.id || "").length > 28
            ? (ln.data.label || ln.data.id || "").slice(0, 26) + "..."
            : (ln.data.label || ln.data.id || "");
          const kindBadge = kind.length > 14 ? kind.slice(0, 12) + ".." : kind;

          return (
            <g key={`node-${i}`}>
              <rect
                x={ln.x} y={ln.y}
                width={ln.width} height={ln.height}
                rx="8" ry="8"
                fill={color.bg}
                opacity="0.9"
                stroke={color.border}
                strokeWidth="1.5"
              />
              {/* Kind badge */}
              <rect
                x={ln.x + 6} y={ln.y + 6}
                width={kindBadge.length * 7.5 + 10} height={16}
                rx="4" ry="4"
                fill="rgba(255,255,255,0.2)"
              />
              <text
                x={ln.x + 6 + (kindBadge.length * 7.5 + 10) / 2}
                y={ln.y + 17}
                textAnchor="middle"
                fill={color.text}
                fontSize="9"
                fontWeight="600"
                opacity="0.9"
              >
                {kindBadge}
              </text>
              {/* Label */}
              <text
                x={ln.x + ln.width / 2}
                y={ln.y + 38}
                textAnchor="middle"
                fill={color.text}
                fontSize="11"
                fontWeight="500"
              >
                {label}
              </text>
            </g>
          );
        })}
      </svg>
    </div>
  );
}
