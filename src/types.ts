import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  AlertTriangle,
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  Clapperboard,
  Code2,
  Database,
  Eraser,
  FileText,
  Folder,
  Globe2,
  Languages,
  LayoutDashboard,
  Menu,
  PanelRightClose,
  MessageSquare,
  Plus,
  Pencil,
  RefreshCcw,
  Save,
  Search,
  Send,
  Server,
  ShieldCheck,
  Trash2,
  User,
  X,
} from 'lucide-react';
import { parse } from 'smol-toml';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';

/** Primary API base URL — set at app init via resolveApiBase(). */
export let API_BASE: string =
  (import.meta as any).env?.VITE_API_BASE ||
  (typeof window !== 'undefined' ? `${window.location.protocol}//${window.location.hostname}:8000` : 'http://127.0.0.1:8000');

/** API_FALLBACK_PORTS tried when VITE_API_BASE is not explicitly configured. */
const API_FALLBACK_PORTS = [8000, 8001, 8080, 3001];

/**
 * Probe candidate backend URLs and return the first one that responds.
 * Updates the module-level API_BASE on success.
 * Runs once at app startup — call from a top-level useEffect.
 */
export async function initializeApiBase(envBase?: string): Promise<string> {
  const hostname = typeof window !== 'undefined'
    ? window.location.hostname
    : '127.0.0.1';

  // If user explicitly set VITE_API_BASE to a non-default value, trust it
  if (envBase && envBase !== 'http://localhost:8000') {
    API_BASE = envBase;
    return API_BASE;
  }

  const candidates: string[] = envBase
    ? [envBase, ...API_FALLBACK_PORTS.map(p => `http://${hostname}:${p}`)]
    : API_FALLBACK_PORTS.map(p => `http://${hostname}:${p}`);
  const unique = [...new Set(candidates)];

  for (const base of unique) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 1500);
      const resp = await fetch(`${base}/runtime_status`, { signal: controller.signal, mode: 'cors' });
      clearTimeout(timer);
      if (resp.ok) {
        const data = await resp.json();
        if (data?.ok) {
          API_BASE = base;
          console.log(`[openMegatron] Backend found at ${base} (${data.skills?.loaded ?? '?'}/${data.skills?.total ?? '?'} skills)`);
          return base;
        }
      }
    } catch { /* probe next */ }
  }
  console.warn('[openMegatron] No backend found on known ports, using default.');
  return API_BASE;
}

/**
 * Fetch wrapper with exponential-backoff retry on 502/503/504 and network errors.
 */
export async function fetchWithRetry(
  input: RequestInfo,
  init?: RequestInit,
  maxRetries = 3,
  baseDelayMs = 500,
): Promise<Response> {
  let lastError: unknown = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(input, init);
      if (attempt < maxRetries && (response.status === 502 || response.status === 503 || response.status === 504)) {
        const delay = baseDelayMs * Math.pow(2, attempt);
        console.warn(`[openMegatron] ${response.status}, retry ${attempt + 1}/${maxRetries} in ${delay}ms`);
        await new Promise(r => setTimeout(r, delay));
        continue;
      }
      return response;
    } catch (err: any) {
      lastError = err;
      if (attempt < maxRetries) {
        const delay = baseDelayMs * Math.pow(2, attempt);
        console.warn(`[openMegatron] Fetch failed, retry ${attempt + 1}/${maxRetries}: ${err.message}`);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
  throw lastError || new Error('Fetch failed after retries');
}

export const HISTORY_KEY = 'megatron_chat_history';
export const WORKSPACE_KEY = 'megatron_workspace_state_v1';
export const DEFAULT_PROJECT_TITLE = 'Local Workspace';
export const DEFAULT_CONVERSATION_TITLE = 'New conversation';
export const linkValidationCache = new Map<string, { status: LinkValidationStatus; code?: number }>();

export type Lang = 'zh' | 'en';
export type SkillKey = 'research' | 'code' | 'mediaVideo' | 'watch';

export interface TaskItem {
  name: string;
  icon: string;
}

export interface ConfigText {
  hubTitle: string;
  hubSub: string;
  activeTasks: string;
  completedTasks: string;
  connectedTo: string;
  secureSandbox: string;
  inputHint: string;
  inputPlace: string;
  monitorTitle: string;
  monitorAirGap: string;
  monitorAirDesc: string;
  monitorLockLabel: string;
  monitorLocked: string;
  monitorHardware: string;
  monitorCpu: string;
  monitorMem: string;
  monitorNet: string;
  monitorLocal: string;
  monitorCpuLoad: string;
  monitorBattery: string;
  monitorSafe: string;
  activeTasksList?: TaskItem[];
  completedTasksList?: TaskItem[];
}

export interface Config {
  zh: ConfigText;
  en: ConfigText;
}

export interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  thoughts?: string[];
  isStreaming?: boolean;
}

export interface ChatProject {
  id: string;
  title: string;
  conversationIds: string[];
  createdAt: number;
  updatedAt: number;
}

export interface ChatConversation {
  id: string;
  projectId: string;
  title: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

export interface WorkspaceState {
  projects: Record<string, ChatProject>;
  conversations: Record<string, ChatConversation>;
  activeProjectId: string;
  activeConversationId: string;
}

export interface ConfirmReq {
  request_id?: string;
  prompt: string;
  code_preview: string;
}

export interface ConversationNavItem {
  anchorId: string;
  label: string;
  summary: string;
  kind: 'user' | 'trace' | 'assistant';
}

export interface SkillDraft {
  id: number;
  text: string;
}

export interface RichTextBlock {
  type: 'text' | 'code';
  content: string;
  language?: string;
}

export interface RuntimeServiceStatus {
  name: string;
  label?: string;
  host?: string;
  port?: number | null;
  status?: 'online' | 'offline' | 'unknown';
  latency_ms?: number | null;
  reason?: string;
}

export interface RuntimeStatus {
  ok?: boolean;
  timestamp?: number;
  latency_ms?: number;
  backend?: {
    status?: 'online' | 'offline' | 'unknown';
    agent_status?: 'ready' | 'degraded';
    startup_error?: string | null;
    pid?: number;
    uptime_sec?: number;
    host?: string;
    port?: number | null;
    python?: string;
  };
  system?: {
    platform?: string;
    logical_cores?: number;
    cpu_percent?: number;
    memory_percent?: number;
    memory_used_mb?: number;
    memory_total_mb?: number;
    disk_percent?: number;
    disk_free_gb?: number;
    psutil_available?: boolean;
  };
  process?: {
    pid?: number;
    cpu_percent?: number;
    memory_mb?: number;
    threads?: number;
  };
  services?: RuntimeServiceStatus[];
  skills?: {
    total?: number;
    loaded?: number;
    categories?: Record<string, number>;
  };
}

export interface ModelOption {
  id: string;
  name?: string;
}

export interface ModelProviderOption {
  id: string;
  label: string;
  baseUrl?: string;
  configured?: boolean;
  models: ModelOption[];
}

export interface ModelOptionsResponse {
  active_provider?: string;
  active_model?: string;
  providers?: ModelProviderOption[];
}

export interface ModelSelection {
  provider: string;
  model: string;
}

export interface MemoryRecord {
  id: string;
  text: string;
  owner_id?: string;
  scope?: string;
  session_id?: string;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
}

export interface MemoryRecordsResponse {
  records?: MemoryRecord[];
  total?: number;
  status?: string;
  message?: string;
}

export interface EvolutionFileChange {
  path: string;
  action?: 'write' | 'delete';
  content?: string;
  summary?: string;
  size?: number;
  content_hash?: string;
  truncated?: boolean;
}

export interface EvolutionProposal {
  id: string;
  kind?: string;
  title: string;
  summary: string;
  author?: string;
  status: 'proposed' | 'applied' | 'rejected' | 'rolled_back' | string;
  created_at?: string;
  updated_at?: string;
  applied_at?: string;
  rolled_back_at?: string;
  files?: EvolutionFileChange[];
  notes?: string[];
}

export interface EvolutionProposalsResponse {
  status?: string;
  message?: string;
  proposals?: EvolutionProposal[];
  total?: number;
}

export interface MemoryGraphNode {
  id: string;
  label?: string;
  type?: string;
  text?: string;
  metadata?: Record<string, unknown> | null;
}

export interface MemoryGraphEdge {
  id?: string;
  source: string;
  target: string;
  label?: string;
  type?: string;
  metadata?: Record<string, unknown> | null;
}

export interface MemoryGraphResponse {
  status?: string;
  message?: string;
  nodes?: MemoryGraphNode[];
  edges?: MemoryGraphEdge[];
}

export type LinkValidationStatus = 'idle' | 'checking' | 'ok' | 'bad' | 'unknown';

export const MODEL_SELECTION_KEY = 'megatron_model_selection_v1';

export const DEFAULT_MODEL_PROVIDERS: ModelProviderOption[] = [
  { id: 'openai', label: 'OpenAI', baseUrl: 'https://api.openai.com/v1', models: [{ id: 'gpt-4.1' }, { id: 'gpt-4.1-mini' }, { id: 'gpt-4o' }, { id: 'gpt-4o-mini' }, { id: 'o4-mini' }] },
  { id: 'deepseek', label: 'DeepSeek', baseUrl: 'https://api.deepseek.com', models: [{ id: 'deepseek-chat' }, { id: 'deepseek-reasoner' }] },
  { id: 'qwen', label: '通义千问 / Qwen', baseUrl: 'https://dashscope.aliyuncs.com/compatible-mode/v1', models: [{ id: 'qwen-plus' }, { id: 'qwen-max' }, { id: 'qwen-turbo' }, { id: 'qwen-long' }] },
  { id: 'moonshot', label: 'Moonshot', baseUrl: 'https://api.moonshot.cn/v1', models: [{ id: 'moonshot-v1-8k' }, { id: 'moonshot-v1-32k' }, { id: 'moonshot-v1-128k' }] },
  { id: 'zhipu', label: '智谱 / Zhipu', baseUrl: 'https://open.bigmodel.cn/api/paas/v4', models: [{ id: 'glm-4-flash' }, { id: 'glm-4-plus' }, { id: 'glm-4-air' }] },
  { id: 'minimax', label: 'MiniMax', baseUrl: 'https://api.minimax.chat/v1', models: [{ id: 'MiniMax-Text-01' }, { id: 'MiniMax-M1' }] },
  { id: 'stepfun', label: '阶跃星辰 / Stepfun', baseUrl: 'https://api.stepfun.com/v1', models: [{ id: 'step-2-mini' }, { id: 'step-2-16k' }, { id: 'step-1-8k' }] },
  { id: 'siliconflow', label: '硅基流动 / SiliconFlow', baseUrl: 'https://api.siliconflow.cn/v1', models: [{ id: 'Qwen/Qwen2.5-72B-Instruct' }, { id: 'deepseek-ai/DeepSeek-V3' }, { id: 'deepseek-ai/DeepSeek-R1' }] },
  { id: 'openrouter', label: 'OpenRouter', baseUrl: 'https://openrouter.ai/api/v1', models: [{ id: 'openai/gpt-4o-mini' }, { id: 'anthropic/claude-3.5-sonnet' }, { id: 'google/gemini-2.0-flash-001' }] },
];

export const DEFAULT_CONFIG: Config = {
  zh: {
    hubTitle: '源哥 AI',
    hubSub: '本地智能体工作台',
    activeTasks: '正在推进',
    completedTasks: '最近完成',
    connectedTo: '连接',
    secureSandbox: '安全执行通道',
    inputHint: '本地会话已就绪',
    inputPlace: '给智能体一个任务...',
    monitorTitle: '运行状态',
    monitorAirGap: '本地受控执行',
    monitorAirDesc: '后端、数据库和技能目录都在本机项目环境中运行，敏感配置不会在前端展示。',
    monitorLockLabel: '后端状态',
    monitorLocked: '待命',
    monitorHardware: '资源概览',
    monitorCpu: '处理负载',
    monitorMem: '内存占用',
    monitorNet: '磁盘占用',
    monitorLocal: '本机环境',
    monitorCpuLoad: '技能数量',
    monitorBattery: '响应延迟',
    monitorSafe: '工作区已就绪',
    activeTasksList: [
      { name: '科研综述链路', icon: 'FileText' },
      { name: '码农技能路由', icon: 'Code2' },
      { name: '媒体处理（含视频制作）', icon: 'Clapperboard' },
    ],
    completedTasksList: [
      { name: '顶刊顶会检索', icon: 'CheckCircle2' },
      { name: '研究空白分析', icon: 'CheckCircle2' },
      { name: '引用格式与校验', icon: 'CheckCircle2' },
    ],
  },
  en: {
    hubTitle: 'YuanGe AI',
    hubSub: 'Local Agent Workspace',
    activeTasks: 'In Progress',
    completedTasks: 'Recent',
    connectedTo: 'Connected',
    secureSandbox: 'Guarded Execution',
    inputHint: 'Local session ready',
    inputPlace: 'Give the agent a task...',
    monitorTitle: 'Runtime',
    monitorAirGap: 'Controlled Local Runtime',
    monitorAirDesc: 'The backend, databases, and skill folders run inside this local project. Sensitive config is never shown here.',
    monitorLockLabel: 'Backend',
    monitorLocked: 'Standby',
    monitorHardware: 'Resources',
    monitorCpu: 'Compute Load',
    monitorMem: 'Memory Use',
    monitorNet: 'Disk Use',
    monitorLocal: 'Local Runtime',
    monitorCpuLoad: 'Skills',
    monitorBattery: 'Latency',
    monitorSafe: 'Workspace Ready',
    activeTasksList: [
      { name: 'Research Review Pipeline', icon: 'FileText' },
      { name: 'Code Assistant Router', icon: 'Code2' },
      { name: 'Media Processing + Video', icon: 'Clapperboard' },
    ],
    completedTasksList: [
      { name: 'Top Venue Search', icon: 'CheckCircle2' },
      { name: 'Research Gap Analysis', icon: 'CheckCircle2' },
      { name: 'Citation Styles & Checks', icon: 'CheckCircle2' },
    ],
  },
};

export const quickPrompts: Record<Lang, string[]> = {
  zh: [
    '用科研技能查找 LLM 智能体记忆的顶会顶刊论文，并给出证据边界',
    '帮我把这段回答中的 DOI 和链接逐条验证',
    '基于两篇论文生成证据矩阵和研究空白',
  ],
  en: [
    'Use the research skills to find top-tier papers about memory in LLM agents and state the evidence boundary.',
    'Verify the DOIs and links in this answer one by one.',
    'Build an evidence matrix and research-gap analysis from two papers.',
  ],
};

export const skillTestPrompts: Record<Lang, Record<SkillKey, string>> = {
  zh: {
    research: '请测试科研技能链路：用内置科研技能完成一次离线 smoke test，优先调用 paper_reader、evidence_matrix、citation_verifier、citation_graph、journal_matcher；不要依赖外网，最后按技能逐项给出通过/失败和原因。',
    code: '请测试代码技能：检查当前项目结构，说明可运行的构建、类型检查和后端自测命令。',
    mediaVideo: '请测试媒体/视频技能：说明当前可用的视频搜索、下载、转码或 storyboard 视频制作能力，并给出一个最小测试参数。',
    watch: '请测试监控技能：列出可配置的博客/RSS/科研来源，并说明如何启动一次只读测试。',
  },
  en: {
    research: 'Test the research skill chain with an offline smoke test. Prefer paper_reader, evidence_matrix, citation_verifier, citation_graph, and journal_matcher; do not depend on the network. Report pass/fail per skill with reasons.',
    code: 'Test the code skill by checking the project structure and listing runnable build, type-check, and backend self-test commands.',
    mediaVideo: 'Test the media/video skills by describing the available search, download, transcode, or storyboard-video capability and provide minimal test parameters.',
    watch: 'Test the monitoring skill by listing configured blog/RSS/research sources and explaining one read-only test run.',
  },
};

