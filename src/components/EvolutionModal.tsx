// EvolutionModal.tsx - Full evolution console with 4 tabs
import React, { useCallback, useEffect, useState } from "react";
import {
  RefreshCcw, Save, ShieldCheck, X, BarChart3, GitBranch,
  Target, FileText, Layers, Activity, TrendingUp, TrendingDown
} from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { API_BASE } from "../types.ts";
import type { EvolutionProposal, EvolutionProposalsResponse, Lang } from "../types.ts";
import { formatMemoryTime, guessCodeLanguage } from "./MemoryModal.tsx";

// ── Visualization Types ────────────────────────────────────────────────────
interface CategoryState {
  name: string;
  level: string;
  level_value: number;
  level_color: string;
  success_rate: number;
  total_executions: number;
  demotion_count: number;
  is_frozen: boolean;
  frozen_remaining_seconds: number;
  consecutive_successes: number;
  pre_checks_enabled: boolean;
  exploration_enabled: boolean;
}

interface EvolutionDashboard {
  categories: CategoryState[];
  evolution_stack: Record<string, number>;
  radar_chart: Array<{ category: string; level: string; axes: any[] }>;
  total_categories: number;
  average_level: number;
}

interface HypergraphNode {
  id: string;
  kind: string;
  label: string;
  visual: { color: string; size: number; shape: string; opacity?: number };
}

interface HypergraphLink {
  source: string;
  target: string;
  relation: string;
  weight: number;
  color: string;
  width: number;
}

interface EvolutionHypergraph {
  nodes: HypergraphNode[];
  links: HypergraphLink[];
  metadata: { total_nodes: number; total_links: number; node_types: string[] };
}

interface CausalForestRow {
  category: string;
  transition: string;
  att: number;
  ci_low: number;
  ci_high: number;
  significant: boolean;
  was_randomized: boolean;
  post_executions: number;
}

interface CausalForest {
  rows: CausalForestRow[];
  summary: {
    total_analyzed: number;
    significant_count: number;
    positive_count: number;
    negative_count: number;
    mean_att: number;
  };
}

interface ParetoPoint {
  id: string;
  category: string;
  level: string;
  x: number; y: number; z: number;
  color_value: number;
  level_color: string;
  metrics: Record<string, number>;
}

interface ParetoFrontier {
  points: ParetoPoint[];
  ideal_point: { x: number; y: number; z: number } | null;
  nadir_point: { x: number; y: number; z: number } | null;
  frontier_size: number;
}

interface EvolutionVisualization {
  status: string;
  dashboard: EvolutionDashboard;
  hypergraph: EvolutionHypergraph;
  causal_forest: CausalForest;
  pareto_frontier: ParetoFrontier;
}

type TabId = "dashboard" | "proposals" | "hypergraph" | "causal";

const TABS: { id: TabId; icon: any; label_en: string; label_zh: string }[] = [
  { id: "dashboard", icon: BarChart3, label_en: "Dashboard", label_zh: "进化仪表盘" },
  { id: "proposals", icon: FileText, label_en: "Proposals", label_zh: "提案审查" },
  { id: "hypergraph", icon: GitBranch, label_en: "Ontology Graph", label_zh: "本体超图" },
  { id: "causal", icon: Target, label_en: "Causal Analysis", label_zh: "因果分析" },
];

// ── Color Constants ─────────────────────────────────────────────────────────
const OBJECTIVE_COLORS: Record<string, string> = {
  success_rate: "#2ECC71",
  efficiency: "#3498DB",
  cost_effective: "#E74C3C",
  stability: "#9B59B6",
};

const LEVEL_COLORS: Record<string, string> = {
  REACTIVE: "#95A5A6",
  PREDICTIVE: "#3498DB",
  EXPLORATION: "#9B59B6",
  AUTONOMOUS: "#F1C40F",
};

// ── Component: Dashboard Tab ───────────────────────────────────────────────
function DashboardTab({ lang, dashboard }: { lang: Lang; dashboard: EvolutionDashboard }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {/* Evolution Tower */}
      <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--text-main)]">
          <Layers className="h-4 w-4 text-[var(--accent)]" />
          {lang === "zh" ? "进化层级分布" : "Evolution Level Distribution"}
        </div>
        <div className="flex items-end justify-around gap-2 h-48">
          {Object.entries(dashboard.evolution_stack || {}).map(([level, count]) => (
            <div key={level} className="flex flex-col items-center gap-1">
              <div
                className="w-12 rounded-t-lg transition-all duration-500"
                style={{
                  height: `${Math.max(8, count * 20)}px`,
                  backgroundColor: LEVEL_COLORS[level] || "#95A5A6",
                }}
              />
              <div className="text-xs font-semibold text-[var(--text-muted)]">{level}</div>
              <div className="text-xs font-bold text-[var(--text-main)]">{count}</div>
            </div>
          ))}
        </div>
      </div>

      {/* Category Status Grid */}
      <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--text-main)]">
          <Activity className="h-4 w-4 text-[var(--accent)]" />
          {lang === "zh" ? "类别状态" : "Category Status"}
        </div>
        <div className="space-y-2 max-h-48 overflow-auto">
          {(dashboard.categories || []).map((cat) => (
            <div key={cat.name} className="flex items-center justify-between p-2 rounded-md bg-[var(--bg-soft)]">
              <div className="flex items-center gap-2">
                <div
                  className="w-3 h-3 rounded-full"
                  style={{ backgroundColor: cat.level_color }}
                />
                <span className="text-sm font-medium text-[var(--text-main)]">{cat.name}</span>
                <span className="text-xs px-1.5 py-0.5 rounded bg-white border border-[var(--border)] text-[var(--text-muted)]">
                  {cat.level}
                </span>
              </div>
              <div className="flex items-center gap-3 text-xs">
                <span className="text-[var(--mint-strong)]">{(cat.success_rate * 100).toFixed(0)}% OK</span>
                <span className="text-[var(--text-muted)]">{cat.total_executions} runs</span>
                {cat.is_frozen && (
                  <span className="text-[var(--danger)]">FROZEN</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* Summary Stats */}
      <div className="lg:col-span-2 rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="grid grid-cols-4 gap-4">
          <div className="text-center">
            <div className="text-2xl font-bold text-[var(--accent)]">{dashboard.total_categories || 0}</div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "技能类别" : "Categories"}</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-[var(--mint-strong)]">{dashboard.average_level || 0}</div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "平均进化层级" : "Avg Level"}</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-[#2ECC71]">
              {dashboard.categories?.reduce((s, c) => s + (c.consecutive_successes || 0), 0) || 0}
            </div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "总连续成功" : "Consecutive Wins"}</div>
          </div>
          <div className="text-center">
            <div className="text-2xl font-bold text-[var(--danger)]">
              {dashboard.categories?.reduce((s, c) => s + (c.demotion_count || 0), 0) || 0}
            </div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "降级次数" : "Demotions"}</div>
          </div>
        </div>
      </div>
    </div>
  );
}

// ── Component: Proposals Tab (existing functionality) ──────────────────────
function ProposalsTab({ lang }: { lang: Lang }) {
  const [proposals, setProposals] = useState<EvolutionProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [targetPath, setTargetPath] = useState('docs/evolution-notes.md');
  const [content, setContent] = useState('');

  const loadProposals = useCallback(async () => {
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/evolution/proposals?include_content=true`, { cache: 'no-store' });
      const data: EvolutionProposalsResponse = await res.json();
      if (res.ok && data.status === 'success') setProposals(data.proposals || []);
      else setProposals([]);
    } catch {
      setProposals([]);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadProposals(); }, [loadProposals]);

  const createProposal = async () => {
    if (!title.trim() || !summary.trim() || !targetPath.trim()) {
      setStatus(lang === 'zh' ? '标题、摘要和路径必填' : 'Title, summary, and path are required');
      return;
    }
    setStatus(lang === 'zh' ? '正在创建提案...' : 'Creating proposal...');
    try {
      const res = await fetch(`${API_BASE}/evolution/proposals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: title.trim(), summary: summary.trim(), kind: 'project', author: 'frontend',
          files: [{ path: targetPath.trim(), action: 'write', content, summary: summary.trim() }],
          notes: [lang === 'zh' ? '由前端审查面板创建' : 'Created from the frontend review panel'],
        }),
      });
      const data = await res.json();
      if (res.ok && data.status === 'success') {
        setTitle(''); setSummary(''); setContent('');
        setStatus(lang === 'zh' ? '提案已创建' : 'Proposal created');
        await loadProposals();
      } else {
        throw new Error(data.message || 'create failed');
      }
    } catch (error) {
      setStatus(error instanceof Error ? error.message : (lang === 'zh' ? '创建失败' : 'Create failed'));
    }
  };

  const runProposalAction = async (proposal: EvolutionProposal, action: 'apply' | 'reject' | 'rollback') => {
    const labels = { apply: lang === 'zh' ? '应用' : 'apply', reject: lang === 'zh' ? '拒绝' : 'reject', rollback: lang === 'zh' ? '回滚' : 'rollback' };
    if (!window.confirm(lang === 'zh' ? `确认${labels[action]}这个提案？` : `Confirm ${labels[action]} this proposal?`)) return;
    setStatus(lang === 'zh' ? `正在${labels[action]}...` : `Running ${labels[action]}...`);
    try {
      const res = await fetch(`${API_BASE}/evolution/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: proposal.id, reviewer: 'frontend' }),
      });
      const data = await res.json();
      if (res.ok && data.status === 'success') setStatus(lang === 'zh' ? '操作完成' : 'Action complete');
      else throw new Error(data.message || `${action} failed`);
      await loadProposals();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : (lang === 'zh' ? '操作失败' : 'Action failed'));
    }
  };

  return (
    <div className="space-y-0">
      {status && <div className="mb-3 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{status}</div>}
      <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
        {/* New Proposal Form */}
        <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-3 text-sm font-semibold text-[var(--text-main)]">{lang === 'zh' ? '新建提案' : 'New proposal'}</div>
          <div className="space-y-3">
            <input value={title} onChange={(e) => setTitle(e.target.value)}
              className="h-10 w-full rounded-lg border border-[var(--border)] px-3 text-sm outline-none focus:border-[var(--accent)]"
              placeholder={lang === 'zh' ? '标题' : 'Title'} />
            <input value={targetPath} onChange={(e) => setTargetPath(e.target.value)}
              className="h-10 w-full rounded-lg border border-[var(--border)] px-3 font-mono text-sm outline-none focus:border-[var(--accent)]"
              placeholder="docs/evolution-notes.md" />
            <textarea value={summary} onChange={(e) => setSummary(e.target.value)}
              className="min-h-20 w-full resize-y rounded-lg border border-[var(--border)] p-3 text-sm leading-6 outline-none focus:border-[var(--accent)]"
              placeholder={lang === 'zh' ? '摘要：为什么要改' : 'Summary: why this change matters'} />
            <textarea value={content} onChange={(e) => setContent(e.target.value)}
              className="min-h-40 w-full resize-y rounded-lg border border-[var(--border)] p-3 font-mono text-xs leading-5 outline-none focus:border-[var(--accent)]"
              placeholder={lang === 'zh' ? '目标文件的新内容' : 'Full replacement content for the target file'} />
            <button className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-[var(--accent)] px-3 text-sm text-white hover:bg-[var(--accent-strong)]" onClick={createProposal} disabled={loading}>
              <Save className="h-4 w-4" />
              {lang === 'zh' ? '创建审查提案' : 'Create proposal'}
            </button>
          </div>
        </div>

        {/* Proposal List */}
        <div className="min-w-0">
          <div className="mb-3 flex items-center justify-between gap-3">
            <div className="text-sm font-semibold text-[var(--text-main)]">
              {lang === 'zh' ? `提案列表 (${proposals.length})` : `Proposals (${proposals.length})`}
            </div>
            <button className="inline-flex h-9 items-center gap-2 rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--bg-soft)]" onClick={loadProposals} disabled={loading}>
              <RefreshCcw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {lang === 'zh' ? '刷新' : 'Refresh'}
            </button>
          </div>
          {proposals.length === 0 ? (
            <div className="rounded-lg border border-dashed border-[var(--border-strong)] p-8 text-center text-sm text-[var(--text-muted)]">
              {loading ? (lang === 'zh' ? '加载中...' : 'Loading...') : (lang === 'zh' ? '暂无进化提案' : 'No evolution proposals yet')}
            </div>
          ) : (
            <div className="space-y-3 max-h-[52vh] overflow-auto pr-1">
              {proposals.map((p) => (
                <EvolutionProposalCard key={p.id} lang={lang} proposal={p} onAction={runProposalAction} />
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

// ── Component: Hypergraph Tab ──────────────────────────────────────────────
function HypergraphTab({ lang, graph }: { lang: Lang; graph: EvolutionHypergraph }) {
  const [selectedNode, setSelectedNode] = useState<HypergraphNode | null>(null);

  // For now, show a node list since force-graph requires adding a dependency
  // In production, add react-force-graph-2d for interactive visualization
  return (
    <div className="grid gap-4 lg:grid-cols-[220px_minmax(0,1fr)]">
      {/* Node Legend / Filter */}
      <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="mb-3 text-sm font-semibold text-[var(--text-main)]">
          {lang === "zh" ? "节点类型" : "Node Types"}
        </div>
        <div className="space-y-2 text-xs">
          {Object.entries(graph.metadata.node_types?.reduce((acc, t) => ({ ...acc, [t]: (acc[t] || 0) + 1 }), {} as Record<string, number>) || {}).map(([kind, count]) => (
            <div key={kind} className="flex items-center justify-between">
              <span className="text-[var(--text-muted)] capitalize">{kind.replace("_", " ")}</span>
              <span className="font-bold text-[var(--text-main)]">{count}</span>
            </div>
          ))}
        </div>
        <div className="mt-4 pt-4 border-t border-[var(--border)]">
          <div className="text-xs text-[var(--text-muted)] mb-2">{lang === "zh" ? "统计" : "Stats"}</div>
          <div className="text-xs"><span className="text-[var(--text-muted)]">Nodes:</span> <span className="font-bold text-[var(--text-main)]">{graph.metadata.total_nodes}</span></div>
          <div className="text-xs"><span className="text-[var(--text-muted)]">Links:</span> <span className="font-bold text-[var(--text-main)]">{graph.metadata.total_links}</span></div>
        </div>
      </div>

      {/* Interactive Graph Placeholder + Node List */}
      <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="mb-3 text-sm font-semibold text-[var(--text-main)]">
          {lang === "zh" ? "进化本体超图" : "Evolution Ontology Hypergraph"}
        </div>
        <div className="mb-3 p-3 rounded-md bg-[var(--bg-soft)] text-xs text-[var(--text-muted)]">
          {lang === "zh"
            ? "💡 安装 react-force-graph-2d 可启用交互式力导向可视化。当前显示节点列表。"
            : "💡 Install react-force-graph-2d for interactive force-directed visualization. Showing node list for now."}
        </div>
        <div className="max-h-[40vh] overflow-auto space-y-1">
          {(graph.nodes || []).map((node) => (
            <div
              key={node.id}
              className="flex items-center gap-2 p-2 rounded-md text-xs cursor-pointer hover:bg-[var(--bg-soft)] transition-colors"
              onClick={() => setSelectedNode(node)}
              style={{ borderLeft: `3px solid ${node.visual?.color || '#999'}` }}
            >
              <div
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: node.visual?.color || '#999', opacity: node.visual?.opacity || 1 }}
              />
              <span className="font-mono text-[var(--text-muted)] uppercase">{node.kind}</span>
              <span className="text-[var(--text-main)] truncate">{node.label}</span>
            </div>
          ))}
        </div>
        {selectedNode && (
          <div className="mt-4 p-3 rounded-md border border-[var(--border)] bg-[var(--bg-soft)]">
            <div className="text-xs font-semibold text-[var(--text-main)] mb-1">{selectedNode.id}</div>
            <div className="text-xs text-[var(--text-muted)]">
              Kind: <span className="text-[var(--text-main)] font-mono uppercase">{selectedNode.kind}</span>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Component: Causal Analysis Tab ─────────────────────────────────────────
function CausalTab({ lang, causal, pareto }: { lang: Lang; causal: CausalForest; pareto: ParetoFrontier }) {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      {/* Forest Plot */}
      <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--text-main)]">
          <Target className="h-4 w-4 text-[var(--accent)]" />
          {lang === "zh" ? "因果效应森林图" : "Causal Treatment Effect (Forest Plot)"}
        </div>
        {(causal.rows || []).length === 0 ? (
          <div className="p-8 text-center text-sm text-[var(--text-muted)]">
            {lang === "zh" ? "暂无晋升数据，因果分析需要至少一次晋升" : "No promotions analyzed yet. Run evolution to populate this view."}
          </div>
        ) : (
          <div className="space-y-2 max-h-[40vh] overflow-auto">
            {(causal.rows || []).map((row, i) => {
              const effectSize = Math.min(40, Math.abs(row.att) * 200);
              return (
                <div key={i} className="flex items-center gap-2 p-2 rounded-md bg-[var(--bg-soft)]">
                  <div className="w-16 text-xs text-[var(--text-main)] font-mono truncate">{row.category}</div>
                  <div className="flex-1 relative h-4 flex items-center justify-center">
                    {/* Zero line */}
                    <div className="absolute left-1/2 w-px h-full bg-[var(--border)]" />
                    {/* CI Bar */}
                    <div
                      className="absolute h-1 rounded-full"
                      style={{
                        left: `calc(50% + ${(row.ci_low / 0.5) * 100}%)`,
                        right: `calc(50% - ${(row.ci_high / 0.5) * 100}%)`,
                        backgroundColor: row.significant
                          ? (row.att > 0 ? "#2ECC71" : "#E74C3C")
                          : "#95A5A6",
                        opacity: row.significant ? 0.8 : 0.4,
                      }}
                    />
                    {/* ATT Point */}
                    <div
                      className="absolute w-2.5 h-2.5 rounded-full border border-white"
                      style={{
                        left: `calc(50% + ${(row.att / 0.5) * 100}% - 5px)`,
                        backgroundColor: row.significant
                          ? (row.att > 0 ? "#2ECC71" : "#E74C3C")
                          : "#95A5A6",
                      }}
                    />
                  </div>
                  <div className="w-20 text-right text-xs font-mono">
                    ATT = {row.att >= 0 ? "+" : ""}{(row.att * 100).toFixed(1)}%
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {/* Summary Stats */}
        <div className="mt-4 pt-4 border-t border-[var(--border)] grid grid-cols-4 gap-2">
          <div className="text-center p-2 rounded-md bg-[var(--bg-soft)]">
            <div className="text-lg font-bold text-[var(--accent)]">{causal.summary?.total_analyzed || 0}</div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "总晋升" : "Promotions"}</div>
          </div>
          <div className="text-center p-2 rounded-md bg-[var(--bg-soft)]">
            <div className="text-lg font-bold text-[var(--mint-strong)]">{causal.summary?.significant_count || 0}</div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "显著" : "Significant"}</div>
          </div>
          <div className="text-center p-2 rounded-md bg-[var(--bg-soft)]">
            <div className="text-lg font-bold text-[#2ECC71]">{causal.summary?.positive_count || 0}</div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "正效应" : "Helped"}</div>
          </div>
          <div className="text-center p-2 rounded-md bg-[var(--bg-soft)]">
            <div className="text-lg font-bold text-[#E74C3C]">{causal.summary?.negative_count || 0}</div>
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "负效应" : "Hurt"}</div>
          </div>
        </div>
      </div>

      {/* Pareto Frontier Summary */}
      <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
        <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-[var(--text-main)]">
          <TrendingUp className="h-4 w-4 text-[var(--accent)]" />
          {lang === "zh" ? "帕累托最优前沿" : "Pareto Optimal Frontier"}
        </div>
        <div className="p-3 rounded-md bg-[var(--bg-soft)] text-xs text-[var(--text-muted)] mb-3">
          {lang === "zh"
            ? "🎯 4D 目标优化：成功率、效率、成本、稳定性。3D 投影 + 颜色编码第 4 维"
            : "🎯 4D optimization: Success, Efficiency, Cost, Stability. 3D projection + color for 4th dim"}
        </div>
        <div className="space-y-2 max-h-[32vh] overflow-auto">
          {(pareto.points || []).map((p) => (
            <div key={p.id} className="flex items-center gap-2 p-2 rounded-md bg-[var(--bg-soft)]">
              <div className="w-3 h-3 rounded-full" style={{ backgroundColor: p.level_color }} />
              <span className="text-xs font-medium text-[var(--text-main)] w-16">{p.category}</span>
              <span className="text-xs px-1.5 py-0.5 rounded bg-white border border-[var(--border)]">{p.level}</span>
              <div className="flex-1 grid grid-cols-4 gap-1 text-xs font-mono text-right">
                <span className="text-[#2ECC71]">S={(p.metrics?.success_rate * 100).toFixed(0)}%</span>
                <span className="text-[#3498DB]">E={(p.metrics?.efficiency * 100).toFixed(0)}%</span>
                <span className="text-[#E74C3C]">C={(p.metrics?.cost_effective * 100).toFixed(0)}%</span>
                <span className="text-[#9B59B6]">St={(p.metrics?.stability * 100).toFixed(0)}%</span>
              </div>
            </div>
          ))}
        </div>
        {pareto.ideal_point && (
          <div className="mt-4 pt-4 border-t border-[var(--border)]">
            <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "理想点（乌托邦）" : "Ideal (Utopia) Point"}</div>
            <div className="text-xs font-mono text-[var(--text-main)]">
              Success={(pareto.ideal_point.x * 100).toFixed(0)}%, Efficiency={(pareto.ideal_point.y * 100).toFixed(0)}%, Cost={(pareto.ideal_point.z * 100).toFixed(0)}%
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

// ── Evolution Proposal Card (Existing Component) ───────────────────────────
function EvolutionProposalCard({
  lang, proposal, onAction,
}: { lang: Lang; proposal: EvolutionProposal; onAction: (p: EvolutionProposal, a: 'apply' | 'reject' | 'rollback') => void }) {
  const firstFile = proposal.files?.[0];
  const statusClass =
    proposal.status === 'applied'
      ? 'bg-[var(--mint-soft)] text-[var(--mint-strong)]'
      : proposal.status === 'proposed'
        ? 'bg-[var(--bg-soft)] text-[var(--accent)]'
        : proposal.status === 'rejected'
          ? 'bg-[var(--danger-soft)] text-[var(--danger)]'
          : 'bg-[var(--bg-soft)] text-[var(--text-muted)]';

  return (
    <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className={`rounded-md px-2 py-0.5 text-xs font-semibold ${statusClass}`}>{proposal.status}</span>
        <span className="rounded-md bg-[var(--bg-soft)] px-2 py-0.5 text-xs text-[var(--text-muted)]">{proposal.kind || 'project'}</span>
        <span className="min-w-0 truncate font-mono text-xs text-[var(--text-muted)]">{proposal.id}</span>
      </div>
      <div className="text-sm font-semibold text-[var(--text-main)]">{proposal.title}</div>
      <div className="mt-1 whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-muted)]">{proposal.summary}</div>
      <div className="mt-3 flex flex-wrap gap-2 text-xs text-[var(--text-muted)]">
        {proposal.updated_at ? <span>{formatMemoryTime(proposal.updated_at)}</span> : null}
        {proposal.files?.map((file) => (
          <span key={`${proposal.id}-${file.path}`} className="rounded-md bg-[var(--bg-soft)] px-2 py-0.5 font-mono">
            {file.action || 'write'} {file.path}
          </span>
        ))}
      </div>
      {firstFile?.content ? (
        <div className="mt-3 max-h-56 overflow-auto rounded-lg border border-[var(--border)] bg-[#111827]">
          <SyntaxHighlighter language={guessCodeLanguage(firstFile.path)} style={oneDark} customStyle={{ background: 'transparent', margin: 0, padding: '12px', fontSize: '12px' }} wrapLongLines>
            {firstFile.content}
          </SyntaxHighlighter>
        </div>
      ) : null}
      <div className="mt-3 flex flex-wrap justify-end gap-2">
        {proposal.status === 'proposed' ? (
          <>
            <button className="rounded-lg border border-[var(--danger-soft)] px-3 py-1.5 text-sm text-[var(--danger)] hover:bg-[var(--danger-soft)]" onClick={() => onAction(proposal, 'reject')}>
              {lang === 'zh' ? '拒绝' : 'Reject'}
            </button>
            <button className="rounded-lg bg-[var(--mint-strong)] px-3 py-1.5 text-sm text-white hover:bg-[var(--mint)]" onClick={() => onAction(proposal, 'apply')}>
              {lang === 'zh' ? '应用并快照' : 'Apply + snapshot'}
            </button>
          </>
        ) : null}
        {proposal.status === 'applied' ? (
          <button className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--bg-soft)]" onClick={() => onAction(proposal, 'rollback')}>
            {lang === 'zh' ? '回滚' : 'Rollback'}
          </button>
        ) : null}
      </div>
    </div>
  );
}

// ── Main Evolution Manager Modal ────────────────────────────────────────────
export function EvolutionManagerModal({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const [activeTab, setActiveTab] = useState<TabId>("dashboard");
  const [visData, setVisData] = useState<EvolutionVisualization | null>(null);
  const [loadingVis, setLoadingVis] = useState(false);

  const loadVisualization = useCallback(async () => {
    setLoadingVis(true);
    try {
      const res = await fetch(`${API_BASE}/evolution/visualization`, { cache: 'no-store' });
      const data = await res.json();
      if (res.ok && data.status === 'success') {
        setVisData(data);
      }
    } catch {
      // Best effort
    } finally {
      setLoadingVis(false);
    }
  }, []);

  useEffect(() => {
    loadVisualization();
  }, [loadVisualization]);

  // Fallback empty data
  const dashboard = visData?.dashboard || { categories: [], evolution_stack: {}, radar_chart: [], total_categories: 0, average_level: 0 };
  const hypergraph = visData?.hypergraph || { nodes: [], links: [], metadata: { total_nodes: 0, total_links: 0, node_types: [] } };
  const causal_forest = visData?.causal_forest || {
    rows: [],
    summary: { total_analyzed: 0, significant_count: 0, positive_count: 0, negative_count: 0, mean_att: 0 },
  };
  const pareto_frontier = visData?.pareto_frontier || { points: [], ideal_point: null, nadir_point: null, frontier_size: 0 };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-6xl flex-col rounded-lg border border-[var(--border)] bg-white shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-semibold text-[var(--text-main)]">
              <ShieldCheck className="h-4 w-4 text-[var(--accent)]" />
              <span>{lang === 'zh' ? '进化控制台' : 'Evolution Console'}</span>
            </div>
            <div className="mt-1 text-xs text-[var(--text-muted)]">
              {lang === 'zh'
                ? '本体驱动的 7 层自进化系统：反应→预测→探索→帕累托→因果→元进化→自主'
                : '7-level ontology-driven evolution: Reactive→Predictive→Exploration→Pareto→Causal→Meta→Autonomous'}
            </div>
          </div>
          <button className="grid h-8 w-8 place-items-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)]" onClick={onClose} aria-label="Close evolution manager">
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Tab Bar */}
        <div className="flex shrink-0 border-b border-[var(--border)] bg-[var(--bg-soft)]">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const active = activeTab === tab.id;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`flex items-center gap-2 px-4 py-3 text-sm transition-colors border-b-2 ${
                  active
                    ? 'border-[var(--accent)] bg-white text-[var(--text-main)] font-medium'
                    : 'border-transparent text-[var(--text-muted)] hover:text-[var(--text-main)]'
                }`}
              >
                <Icon className="h-4 w-4" />
                {lang === 'zh' ? tab.label_zh : tab.label_en}
              </button>
            );
          })}
        </div>

        {/* Tab Content */}
        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {activeTab === "dashboard" && <DashboardTab lang={lang} dashboard={dashboard} />}
          {activeTab === "proposals" && <ProposalsTab lang={lang} />}
          {activeTab === "hypergraph" && <HypergraphTab lang={lang} graph={hypergraph} />}
          {activeTab === "causal" && <CausalTab lang={lang} causal={causal_forest} pareto={pareto_frontier} />}
        </div>
      </div>
    </div>
  );
}
