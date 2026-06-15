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
  Loader2,
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
  UserPlus,
  Users,
  X,
} from 'lucide-react';
import { parse } from 'smol-toml';
import { Prism as SyntaxHighlighter } from 'react-syntax-highlighter';
import { oneDark } from 'react-syntax-highlighter/dist/esm/styles/prism';
import { CitationGraph, parseCitationMermaid } from './components/citation-graph/index.tsx';
import type { CitationEdge, CitationNode } from './components/citation-graph/index.tsx';

const API_BASE = (import.meta as any).env?.VITE_API_BASE || 'http://localhost:8000';
const HISTORY_KEY = 'megatron_chat_history';

import SkillEditorModal from './components/SkillEditorModal.tsx';
import ChatSearch from './components/ChatSearch.tsx';
import MultiSessionTabs from './components/MultiSessionTabs.tsx';
const WORKSPACE_KEY = 'megatron_workspace_state_v1';
const TASK_QUEUE_KEY = 'megatron_task_queue_v1';
const DEFAULT_PROJECT_TITLE = 'Local Workspace';
const DEFAULT_CONVERSATION_TITLE = 'New conversation';
const linkValidationCache = new Map<string, { status: LinkValidationStatus; code?: number }>();
const MEMBER_COLORS = ['#2563eb', '#059669', '#d97706', '#7c3aed', '#dc2626', '#0891b2'];
const seenAgentEventIds = new Set<string>();

type Lang = 'zh' | 'en';
type SkillKey = 'research' | 'code' | 'mediaVideo' | 'watch';

interface TaskItem {
  name: string;
  icon: string;
}

interface UserTask {
  id: string;
  title: string;
  status: 'active' | 'completed';
  conversationId: string;
  messageId?: string;
  createdAt: number;
  updatedAt: number;
}

interface ConfigText {
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

interface Config {
  zh: ConfigText;
  en: ConfigText;
}

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  userId?: string;
  userName?: string;
  thoughts?: string[];
  isStreaming?: boolean;
}

interface ChatMember {
  id: string;
  name: string;
  color: string;
}

interface ChatProject {
  id: string;
  title: string;
  conversationIds: string[];
  createdAt: number;
  updatedAt: number;
}

interface ChatConversation {
  id: string;
  projectId: string;
  title: string;
  roomId: string;
  isGroup: boolean;
  members: ChatMember[];
  activeUserId: string;
  messages: Message[];
  createdAt: number;
  updatedAt: number;
}

interface WorkspaceState {
  projects: Record<string, ChatProject>;
  conversations: Record<string, ChatConversation>;
  activeProjectId: string;
  activeConversationId: string;
}

interface ConfirmReq {
  request_id?: string;
  prompt: string;
  code_preview: string;
}

interface ConversationNavItem {
  anchorId: string;
  label: string;
  summary: string;
  kind: 'user' | 'trace' | 'assistant';
}

interface SkillDraft {
  id: number;
  text: string;
}

interface RichTextBlock {
  type: 'text' | 'code';
  content: string;
  language?: string;
}

interface RuntimeServiceStatus {
  name: string;
  label?: string;
  host?: string;
  port?: number | null;
  status?: 'online' | 'offline' | 'unknown';
  latency_ms?: number | null;
  reason?: string;
}

interface RuntimeStatus {
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

interface ModelOption {
  id: string;
  name?: string;
}

interface ModelProviderOption {
  id: string;
  label: string;
  baseUrl?: string;
  configured?: boolean;
  models: ModelOption[];
}

interface ModelOptionsResponse {
  active_provider?: string;
  active_model?: string;
  providers?: ModelProviderOption[];
}

interface ModelSelection {
  provider: string;
  model: string;
}

interface MemoryRecord {
  id: string;
  text: string;
  owner_id?: string;
  scope?: string;
  session_id?: string;
  metadata?: Record<string, unknown> | null;
  created_at?: string | null;
}

interface MemoryRecordsResponse {
  records?: MemoryRecord[];
  total?: number;
  status?: string;
  message?: string;
}

interface EvolutionFileChange {
  path: string;
  action?: 'write' | 'delete';
  content?: string;
  summary?: string;
  size?: number;
  content_hash?: string;
  truncated?: boolean;
}

interface EvolutionProposal {
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

interface EvolutionProposalsResponse {
  status?: string;
  message?: string;
  proposals?: EvolutionProposal[];
  total?: number;
}

type LinkValidationStatus = 'idle' | 'checking' | 'ok' | 'bad' | 'unknown';

const MODEL_SELECTION_KEY = 'megatron_model_selection_v1';

const DEFAULT_MODEL_PROVIDERS: ModelProviderOption[] = [
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

const DEFAULT_CONFIG: Config = {
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

const iconMap: Record<string, React.ElementType> = {
  Activity,
  CheckCircle2,
  Clapperboard,
  Code2,
  Database,
  FileText,
  Globe2,
  LayoutDashboard,
  Server,
  ShieldCheck,
};

const quickPrompts: Record<Lang, string[]> = {
  zh: [
    '用顶刊顶会文献写一版智能体记忆综述，并给出研究空白',
    '检索 agent memory 的顶会论文，只返回白名单命中的结果',
    '帮我修复一个前端 TypeScript 报错',
  ],
  en: [
    'Write a top-venue literature review on agent memory and research gaps',
    'Search top-venue papers on agent memory only',
    'Fix a frontend TypeScript error',
  ],
};

const skillTestPrompts: Record<Lang, Record<SkillKey, string>> = {
  zh: {
    research: '测试 Research skill：检索 agent memory 的顶会论文，只返回白名单命中的结果，并说明每条结果为什么命中。',
    code: '测试 Code skill：帮我定位并修复一个前端 TypeScript 报错，最后给出验证命令。',
    mediaVideo: '测试媒体/视频 skill：根据一个三幕分镜生成短视频制作方案，包含镜头、字幕、素材需求和交付步骤。',
    watch: '测试 Watch skill：检查当前项目的运行状态、端口占用和最近日志，给出异常项。',
  },
  en: {
    research: 'Test the Research skill: search top-venue papers on agent memory, return only whitelist hits, and explain why each result matches.',
    code: 'Test the Code skill: find and fix a frontend TypeScript error, then list the verification commands.',
    mediaVideo: 'Test the Media/Video skill: create a short-video production plan from a three-act storyboard, including shots, subtitles, assets, and delivery steps.',
    watch: 'Test the Watch skill: inspect project runtime status, port usage, and recent logs, then report anomalies.',
  },
};

function getIconComponent(iconName: string, className = 'h-4 w-4') {
  const Icon = iconMap[iconName] ?? Activity;
  return <Icon className={className} />;
}

function loadSavedMessages(): Message[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const value = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

function createId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

function createDefaultMember(index = 0): ChatMember {
  return {
    id: createId('user'),
    name: index === 0 ? 'Owner' : `Member ${index + 1}`,
    color: MEMBER_COLORS[index % MEMBER_COLORS.length],
  };
}

function normalizeMembers(value: any): ChatMember[] {
  const members = Array.isArray(value)
    ? value
        .filter((member) => member && typeof member === 'object')
        .map((member, index) => ({
          id: typeof member.id === 'string' && member.id.trim() ? member.id : createId('user'),
          name: typeof member.name === 'string' && member.name.trim() ? member.name.trim() : index === 0 ? 'Owner' : `Member ${index + 1}`,
          color: typeof member.color === 'string' && member.color.trim() ? member.color : MEMBER_COLORS[index % MEMBER_COLORS.length],
        }))
    : [];
  return members.length ? members : [createDefaultMember(0), createDefaultMember(1)];
}

function normalizeConversation(conversation: any): ChatConversation {
  const members = normalizeMembers(conversation?.members);
  const activeUserId = typeof conversation?.activeUserId === 'string' && members.some((member) => member.id === conversation.activeUserId)
    ? conversation.activeUserId
    : members[0].id;
  const id = typeof conversation?.id === 'string' && conversation.id ? conversation.id : createId('chat');
  return {
    id,
    projectId: typeof conversation?.projectId === 'string' && conversation.projectId ? conversation.projectId : '',
    title: typeof conversation?.title === 'string' && conversation.title ? conversation.title : DEFAULT_CONVERSATION_TITLE,
    roomId: typeof conversation?.roomId === 'string' && conversation.roomId ? conversation.roomId : id,
    isGroup: typeof conversation?.isGroup === 'boolean' ? conversation.isGroup : members.length > 1,
    members,
    activeUserId,
    messages: Array.isArray(conversation?.messages) ? conversation.messages : [],
    createdAt: Number(conversation?.createdAt) || Date.now(),
    updatedAt: Number(conversation?.updatedAt) || Date.now(),
  };
}

function createConversation(projectId: string, title = DEFAULT_CONVERSATION_TITLE, messages: Message[] = []): ChatConversation {
  const now = Date.now();
  const firstMember = createDefaultMember(0);
  const secondMember = createDefaultMember(1);
  const id = createId('chat');
  return {
    id,
    projectId,
    title,
    roomId: id,
    isGroup: true,
    members: [firstMember, secondMember],
    activeUserId: firstMember.id,
    messages,
    createdAt: now,
    updatedAt: now,
  };
}

function createWorkspaceState(legacyMessages: Message[] = []): WorkspaceState {
  const now = Date.now();
  const projectId = createId('project');
  const conversation = createConversation(
    projectId,
    legacyMessages.length ? deriveConversationTitle(legacyMessages) : DEFAULT_CONVERSATION_TITLE,
    legacyMessages,
  );
  const project: ChatProject = {
    id: projectId,
    title: DEFAULT_PROJECT_TITLE,
    conversationIds: [conversation.id],
    createdAt: now,
    updatedAt: now,
  };
  return {
    projects: { [projectId]: project },
    conversations: { [conversation.id]: conversation },
    activeProjectId: projectId,
    activeConversationId: conversation.id,
  };
}

function normalizeWorkspaceState(value: any): WorkspaceState | null {
  if (!value || typeof value !== 'object') return null;
  const projects = value.projects && typeof value.projects === 'object' ? value.projects : {};
  const rawConversations = value.conversations && typeof value.conversations === 'object' ? value.conversations : {};
  const conversations = Object.fromEntries(
    Object.entries(rawConversations).map(([id, conversation]) => {
      const normalized = normalizeConversation({ ...(conversation as any), id });
      return [id, normalized];
    }),
  ) as Record<string, ChatConversation>;
  const activeProjectId = typeof value.activeProjectId === 'string' ? value.activeProjectId : '';
  const activeConversationId = typeof value.activeConversationId === 'string' ? value.activeConversationId : '';
  if (!projects[activeProjectId] || !conversations[activeConversationId]) return null;
  return { projects, conversations, activeProjectId, activeConversationId };
}

function loadWorkspaceState(): WorkspaceState {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    const parsed = raw ? normalizeWorkspaceState(JSON.parse(raw)) : null;
    if (parsed) return parsed;
  } catch {
    // Fall back to legacy single-chat history.
  }
  return createWorkspaceState(loadSavedMessages());
}

function saveWorkspaceState(state: WorkspaceState) {
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(state));
}

function normalizeTaskQueue(value: any): UserTask[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((task) => task && typeof task === 'object' && typeof task.title === 'string' && task.title.trim())
    .map((task) => ({
      id: typeof task.id === 'string' && task.id ? task.id : createId('task'),
      title: summarizeMessage(task.title, DEFAULT_CONVERSATION_TITLE),
      status: task.status === 'completed' ? 'completed' : 'active',
      conversationId: typeof task.conversationId === 'string' ? task.conversationId : '',
      messageId: typeof task.messageId === 'string' ? task.messageId : undefined,
      createdAt: Number(task.createdAt) || Date.now(),
      updatedAt: Number(task.updatedAt) || Date.now(),
    }));
}

function loadTaskQueue(): UserTask[] {
  try {
    const raw = localStorage.getItem(TASK_QUEUE_KEY);
    return normalizeTaskQueue(raw ? JSON.parse(raw) : []);
  } catch {
    return [];
  }
}

function saveTaskQueue(tasks: UserTask[]) {
  localStorage.setItem(TASK_QUEUE_KEY, JSON.stringify(tasks));
}

function deriveConversationTitle(messages: Message[]) {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.content.trim());
  if (!firstUserMessage) return DEFAULT_CONVERSATION_TITLE;
  return summarizeMessage(firstUserMessage.content, DEFAULT_CONVERSATION_TITLE);
}

function getConversationSessionId(conversation: ChatConversation | string) {
  const roomId = typeof conversation === 'string' ? conversation : conversation.roomId || conversation.id;
  return `room_${toSafeAnchorPart(roomId)}`;
}

function mergeConfig(parsed: any): Config {
  return {
    zh: { ...DEFAULT_CONFIG.zh, ...(parsed?.zh || {}) },
    en: { ...DEFAULT_CONFIG.en, ...(parsed?.en || {}) },
  };
}

function normalizeModelProviders(value?: ModelProviderOption[]) {
  const providers = Array.isArray(value) && value.length ? value : DEFAULT_MODEL_PROVIDERS;
  return providers
    .filter((provider) => provider?.id && provider?.label)
    .map((provider) => ({
      ...provider,
      models: Array.isArray(provider.models) && provider.models.length ? provider.models : [{ id: provider.id }],
    }));
}

function defaultModelSelection(providers: ModelProviderOption[], preferredProvider?: string, preferredModel?: string): ModelSelection {
  const provider = providers.find((item) => item.id === preferredProvider) || providers[0] || DEFAULT_MODEL_PROVIDERS[0];
  const model = provider.models.find((item) => item.id === preferredModel) || provider.models[0] || { id: 'gpt-4o-mini' };
  return { provider: provider.id, model: model.id };
}

function loadSavedModelSelection(): ModelSelection | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(MODEL_SELECTION_KEY) || 'null');
    if (parsed?.provider && parsed?.model) return { provider: String(parsed.provider), model: String(parsed.model) };
  } catch {
    // Ignore malformed local model selection.
  }
  return null;
}

function toSafeAnchorPart(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-');
}

function getMessageAnchorId(messageId: string) {
  return `message-${toSafeAnchorPart(messageId)}`;
}

function getTraceAnchorId(messageId: string) {
  return `trace-${toSafeAnchorPart(messageId)}`;
}

function getHashAnchor() {
  if (typeof window === 'undefined' || !window.location.hash) return '';
  try {
    return decodeURIComponent(window.location.hash.slice(1));
  } catch {
    return window.location.hash.slice(1);
  }
}

function setHashAnchor(anchorId: string) {
  const nextUrl = `${window.location.pathname}${window.location.search}#${encodeURIComponent(anchorId)}`;
  window.history.replaceState(null, '', nextUrl);
}

function clearHashAnchor() {
  if (!window.location.hash) return;
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
}

function scrollToAnchor(anchorId: string, updateHash = true) {
  const target = document.getElementById(anchorId);
  if (!target) return false;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (updateHash) setHashAnchor(anchorId);
  return true;
}

function summarizeMessage(content: string, fallback: string) {
  const withoutCode = content.replace(/```[\s\S]*?```/g, ' ');
  const summary = withoutCode
    .replace(/[#*_>`|()[\]\[]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!summary) return fallback;
  return summary.length > 64 ? `${summary.slice(0, 64)}...` : summary;
}

function displayWorkspaceTitle(title: string, lang: Lang) {
  if (lang === 'zh') {
    if (title === DEFAULT_PROJECT_TITLE) return '本地工作区';
    if (title === DEFAULT_CONVERSATION_TITLE) return '新对话';
    const projectMatch = title.match(/^Project (\d+)$/);
    if (projectMatch) return `项目 ${projectMatch[1]}`;
  }
  return title;
}

function buildConversationNavItems(messages: Message[], lang: Lang): ConversationNavItem[] {
  let userCount = 0;
  let assistantCount = 0;
  return messages.flatMap((message) => {
    if (message.role === 'user') {
      userCount += 1;
      return [
        {
          anchorId: getMessageAnchorId(message.id),
          label: message.userName || (lang === 'zh' ? `用户 ${userCount}` : `User ${userCount}`),
          summary: summarizeMessage(message.content, message.userName || (lang === 'zh' ? '用户消息' : 'User message')),
          kind: 'user' as const,
        },
      ];
    }

    assistantCount += 1;
    const items: ConversationNavItem[] = [];
    if (message.thoughts?.length) {
      items.push({
        anchorId: getTraceAnchorId(message.id),
        label: lang === 'zh' ? '轨迹' : 'Trace',
        summary: summarizeMessage(message.thoughts.join(' '), lang === 'zh' ? '执行过程' : 'Execution trace'),
        kind: 'trace',
      });
    }
    items.push({
      anchorId: getMessageAnchorId(message.id),
      label: lang === 'zh' ? `回复 ${assistantCount}` : `Reply ${assistantCount}`,
      summary: summarizeMessage(message.content, message.isStreaming ? (lang === 'zh' ? '处理中...' : 'processing...') : lang === 'zh' ? '助手回复' : 'Assistant reply'),
      kind: 'assistant',
    });
    return items;
  });
}

function initialProgressThoughts(lang: Lang) {
  return lang === 'zh'
    ? ['已收到请求，正在连接后端', '正在建立会话上下文']
    : ['Request received, connecting to backend', 'Building session context'];
}

function appendMessageThought(message: Message, thought: string, recentWindow = 8): Message {
  const cleanThought = thought.trim();
  if (!cleanThought) return message;
  const thoughts = message.thoughts || [];
  if (thoughts.slice(-recentWindow).includes(cleanThought)) return message;
  return { ...message, thoughts: [...thoughts, cleanThought] };
}

function formatAgentEventTrace(type: string, payload: any, lang: Lang) {
  const data = payload?.data || {};
  if (type === 'chat_start') {
    return lang === 'zh' ? '后端已接收，正在分析任务' : 'Backend received the request and is analyzing it';
  }
  if (type === 'skill_route') {
    const selected = Array.isArray(data.selected_skills) ? data.selected_skills : [];
    const label = selected.length ? selected.slice(0, 3).join(', ') : (lang === 'zh' ? '通用对话' : 'general chat');
    return lang === 'zh' ? `已选择技能路线：${label}` : `Selected skill route: ${label}`;
  }
  if (type === 'skill_start') {
    return (lang === 'zh' ? '正在执行技能：' : 'Running skill: ') + (data.skill_name || data.name || '');
  }
  if (type === 'skill_end') {
    return (lang === 'zh' ? '技能结束：' : 'Skill finished: ') + (data.skill_name || data.name || '') + ' (' + (data.status || 'completed') + ')';
  }
  if (type === 'tool_start') {
    return (lang === 'zh' ? '正在执行：' : 'Running tool: ') + (data.name || '');
  }
  if (type === 'tool_end') {
    return (lang === 'zh' ? '工具完成：' : 'Tool finished: ') + (data.name || '') + ' (' + (data.status || 'completed') + ')';
  }
  return '';
}

export default function App() {
  const [lang, setLang] = useState<Lang>('zh');
  const [rightPanelOpen, setRightPanelOpen] = useState(false);
  const [leftSidebarOpen, setLeftSidebarOpen] = useState(false);
  const [config, setConfig] = useState<Config>(DEFAULT_CONFIG);
  const [loading, setLoading] = useState(true);
  const [runningConversationIds, setRunningConversationIds] = useState<string[]>([]);
  const [confirmReq, setConfirmReq] = useState<ConfirmReq | null>(null);
  const [workspace, setWorkspace] = useState<WorkspaceState>(loadWorkspaceState);
  const [taskQueue, setTaskQueue] = useState<UserTask[]>(loadTaskQueue);
  const [activeAnchorId, setActiveAnchorId] = useState('');
  const [skillDraft, setSkillDraft] = useState<SkillDraft | null>(null);
  const [modelProviders, setModelProviders] = useState<ModelProviderOption[]>(DEFAULT_MODEL_PROVIDERS);
  const [modelSelection, setModelSelection] = useState<ModelSelection>(() => loadSavedModelSelection() || defaultModelSelection(DEFAULT_MODEL_PROVIDERS, 'openai', 'gpt-4o-mini'));
  const [memoryManagerOpen, setMemoryManagerOpen] = useState(false);
  const [skillEditorOpen, setSkillEditorOpen] = useState(false);
  const [evolutionManagerOpen, setEvolutionManagerOpen] = useState(false);
  const [dataActionMessage, setDataActionMessage] = useState('');
  const [activeBlackboard, setActiveBlackboard] = useState<{steps: Array<{id:string;description:string;status:string;duration_ms:number;strategy:string;result_summary:string;retry_count:number;error:string}>;progress:{total:number;completed:number;failed:number;in_progress:number;pending:number;percent:number};report?:string} | null>(null);
  const [chatSearchMessages, setChatSearchMessages] = useState<Message[]>([]);
  const [chatSearchScrollTo, setChatSearchScrollTo] = useState<string | null>(null);

  useEffect(() => {
    saveWorkspaceState(workspace);
  }, [workspace]);

  useEffect(() => {
    saveTaskQueue(taskQueue);
  }, [taskQueue]);

  useEffect(() => {
    localStorage.setItem(MODEL_SELECTION_KEY, JSON.stringify(modelSelection));
  }, [modelSelection]);

  useEffect(() => {
    fetch('/config.toml')
      .then((res) => res.text())
      .then((tomlStr) => setConfig(mergeConfig(parse(tomlStr))))
      .catch(() => setConfig(DEFAULT_CONFIG))
      .finally(() => setLoading(false));

    const handleResize = () => {
      setRightPanelOpen(window.innerWidth >= 1180);
      if (window.innerWidth >= 800) setLeftSidebarOpen(false);
    };
    handleResize();
    window.addEventListener('resize', handleResize);
    return () => window.removeEventListener('resize', handleResize);
  }, []);

  // WebSocket connection for agent events (scheduled tasks, hitl requests)
  useEffect(() => {
    let ws: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout>;
    let closed = false;

    const connect = () => {
      if (closed) return;
      try {
        const wsUrl = API_BASE.replace(/^http/, 'ws') + '/ws/agent-events';
        ws = new WebSocket(wsUrl);
        ws.onmessage = (event) => {
          try {
            const payload = JSON.parse(event.data);
            const type = payload.type;
            const sessionId = payload.session_id;
            const eventId = typeof payload.event_id === 'string' ? payload.event_id : '';
            if (eventId) {
              if (seenAgentEventIds.has(eventId)) return;
              seenAgentEventIds.add(eventId);
              if (seenAgentEventIds.size > 500) {
                const oldest = seenAgentEventIds.values().next().value;
                if (oldest) seenAgentEventIds.delete(oldest);
              }
            }

            if (type === 'chat_start' || type === 'skill_route' || type === 'skill_start' || type === 'skill_end' || type === 'tool_start' || type === 'tool_end') {
              setWorkspace((prev) => {
                const convId = prev.activeConversationId;
                const conv = prev.conversations[convId];
                if (!conv) return prev;
                if (sessionId && getConversationSessionId(conv) !== sessionId) return prev;
                const lastMsg = conv.messages[conv.messages.length - 1];
                if (!lastMsg || lastMsg.role !== 'assistant') return prev;
                const traceLine = formatAgentEventTrace(type, payload, lang);
                if (!traceLine) return prev;
                const updatedLastMsg = appendMessageThought(lastMsg, traceLine);
                if (updatedLastMsg === lastMsg) return prev;
                const now = Date.now();
                return {
                  ...prev,
                  conversations: {
                    ...prev.conversations,
                    [convId]: { ...conv, messages: conv.messages.map((m, idx) =>
                      idx === conv.messages.length - 1 ? updatedLastMsg : m
                    ), updatedAt: now },
                  },
                };
              });
              return;
            }

            if (type === 'scheduled_task') {
              const prompt = payload.prompt || '';
              if (sessionId && prompt) {
                setWorkspace((prev) => {
                  const convId = prev.activeConversationId;
                  const conv = prev.conversations[convId];
                  if (!conv || getConversationSessionId(conv) !== sessionId) return prev;
                  const msg = {
                    id: 'scheduled-' + Date.now(),
                    role: 'assistant' as const,
                    content: '\u23f0 ' + prompt.slice(0, 200),
                    thoughts: [],
                    isStreaming: false,
                  };
                  const now = Date.now();
                  return {
                    ...prev,
                    conversations: {
                      ...prev.conversations,
                      [convId]: { ...conv, messages: [...conv.messages, msg], updatedAt: now },
                    },
                  };
                });
              }
            }
            if (type === 'blackboard_update') {
              const bb = payload.data?.blackboard || payload.blackboard;
              if (bb) {
                setActiveBlackboard({
                  steps: bb.steps || [],
                  progress: bb.progress || { total: 0, completed: 0, failed: 0, in_progress: 0, pending: 0, percent: 0 },
                  report: payload.data?.report || payload.report || '',
                });
              }
              return;
            }
          } catch {
          }
        };
        ws.onclose = () => {
          if (!closed) reconnectTimer = setTimeout(connect, 5000);
        };
        ws.onerror = () => ws?.close();
      } catch {
        reconnectTimer = setTimeout(connect, 5000);
      }
    };

    connect();
    return () => {
      closed = true;
      ws?.close();
      clearTimeout(reconnectTimer);
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    fetch(`${API_BASE}/model_options`, { cache: 'no-store' })
      .then((res) => (res.ok ? res.json() : Promise.reject(new Error(`HTTP ${res.status}`))))
      .then((data: ModelOptionsResponse) => {
        if (cancelled) return;
        const providers = normalizeModelProviders(data.providers);
        setModelProviders(providers);
        const saved = loadSavedModelSelection();
        if (saved && providers.some((provider) => provider.id === saved.provider && provider.models.some((model) => model.id === saved.model))) {
          setModelSelection(saved);
          return;
        }
        setModelSelection(defaultModelSelection(providers, data.active_provider, data.active_model));
      })
      .catch(() => {
        if (cancelled) return;
        setModelProviders(DEFAULT_MODEL_PROVIDERS);
        setModelSelection((current) => current || defaultModelSelection(DEFAULT_MODEL_PROVIDERS, 'openai', 'gpt-4o-mini'));
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const t = config[lang];
  const projects = useMemo(() => Object.values(workspace.projects).sort((a, b) => b.updatedAt - a.updatedAt), [workspace.projects]);
  const activeProject = workspace.projects[workspace.activeProjectId];
  const activeConversation = workspace.conversations[workspace.activeConversationId];
  const projectConversations = useMemo(() => {
    const ids = activeProject?.conversationIds || [];
    return ids
      .map((id) => workspace.conversations[id])
      .filter((conversation): conversation is ChatConversation => Boolean(conversation))
      .sort((a, b) => b.updatedAt - a.updatedAt);
  }, [activeProject, workspace.conversations]);
  const messages = activeConversation?.messages || [];
  // Keep search index in sync
  useEffect(() => { setChatSearchMessages(messages); }, [messages]);
  const activeMembers = activeConversation?.members || [];
  const activeMember = activeMembers.find((member) => member.id === activeConversation?.activeUserId) || activeMembers[0] || { id: 'local-user', name: 'Owner', color: MEMBER_COLORS[0] };
  const activeSessionId = getConversationSessionId(activeConversation || workspace.activeConversationId);
  const activeChatLoading = runningConversationIds.includes(workspace.activeConversationId);
  const activeTasks = useMemo(() => taskQueue.filter((task) => task.status === 'active').sort((a, b) => b.updatedAt - a.updatedAt), [taskQueue]);
  const completedTasks = useMemo(() => taskQueue.filter((task) => task.status === 'completed').sort((a, b) => b.updatedAt - a.updatedAt).slice(0, 20), [taskQueue]);
  const conversationNavItems = useMemo(() => buildConversationNavItems(messages, lang), [messages, lang]);

  const updateConversationMessages = useCallback((conversationId: string, updater: React.SetStateAction<Message[]>) => {
    setWorkspace((prev) => {
      const conversation = prev.conversations[conversationId];
      if (!conversation) return prev;
      const nextMessages = typeof updater === 'function' ? updater(conversation.messages) : updater;
      const now = Date.now();
      const nextConversation: ChatConversation = {
        ...conversation,
        messages: nextMessages,
        title: conversation.title === DEFAULT_CONVERSATION_TITLE ? deriveConversationTitle(nextMessages) : conversation.title,
        updatedAt: now,
      };
      const nextProject = prev.projects[conversation.projectId]
        ? { ...prev.projects[conversation.projectId], updatedAt: now }
        : undefined;
      return {
        ...prev,
        projects: nextProject ? { ...prev.projects, [nextProject.id]: nextProject } : prev.projects,
        conversations: { ...prev.conversations, [conversation.id]: nextConversation },
      };
    });
  }, []);

  const updateActiveMessages = useCallback(
    (updater: React.SetStateAction<Message[]>) => updateConversationMessages(workspace.activeConversationId, updater),
    [updateConversationMessages, workspace.activeConversationId],
  );

  const handleSelectActiveMember = useCallback((memberId: string) => {
    setWorkspace((prev) => {
      const conversation = prev.conversations[prev.activeConversationId];
      if (!conversation || !conversation.members.some((member) => member.id === memberId)) return prev;
      return {
        ...prev,
        conversations: {
          ...prev.conversations,
          [conversation.id]: { ...conversation, activeUserId: memberId },
        },
      };
    });
  }, [lang]);

  const handleAddActiveMember = useCallback(() => {
    const fallbackName = activeConversation ? `Member ${activeConversation.members.length + 1}` : 'Member';
    const promptedName = window.prompt(lang === 'zh' ? '输入成员名称' : 'Member name', fallbackName);
    if (promptedName === null) return;
    const memberName = (promptedName || fallbackName).trim() || fallbackName;
    setWorkspace((prev) => {
      const conversation = prev.conversations[prev.activeConversationId];
      if (!conversation) return prev;
      const member = {
        id: createId('user'),
        name: memberName,
        color: MEMBER_COLORS[conversation.members.length % MEMBER_COLORS.length],
      };
      return {
        ...prev,
        conversations: {
          ...prev.conversations,
          [conversation.id]: {
            ...conversation,
            isGroup: true,
            members: [...conversation.members, member],
            activeUserId: member.id,
            updatedAt: Date.now(),
          },
        },
      };
    });
  }, [activeConversation, lang]);

  const handleDeleteTask = useCallback((taskId: string) => {
    setTaskQueue((prev) => prev.filter((task) => task.id !== taskId));
  }, []);

  const handleSelectTask = useCallback((task: UserTask) => {
    if (task.conversationId && workspace.conversations[task.conversationId]) {
      setWorkspace((prev) => {
        const conversation = prev.conversations[task.conversationId];
        if (!conversation) return prev;
        return { ...prev, activeProjectId: conversation.projectId, activeConversationId: conversation.id };
      });
      if (task.messageId) {
        window.setTimeout(() => scrollToAnchor(getMessageAnchorId(task.messageId || ''), true), 80);
      }
      if (window.innerWidth < 800) setLeftSidebarOpen(false);
    }
  }, [workspace.conversations]);

  useEffect(() => {
    if (!activeChatLoading || confirmReq) return;
    const interval = window.setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE}/check_confirmation?session_id=${encodeURIComponent(activeSessionId)}`);
        const data = await res.json();
        if (data.pending) {
          setConfirmReq({ request_id: data.request_id, prompt: data.prompt, code_preview: data.code_preview });
        }
      } catch {
        // Backend may still be starting.
      }
    }, 1500);
    return () => window.clearInterval(interval);
  }, [activeChatLoading, confirmReq, activeSessionId]);

  useEffect(() => {
    setConfirmReq(null);
  }, [activeSessionId]);

  const handleNavigateToAnchor = useCallback((anchorId: string) => {
    if (scrollToAnchor(anchorId) && window.innerWidth < 1280) {
      setRightPanelOpen(false);
    }
  }, []);

  const handleTestSkill = useCallback(
    (skill: SkillKey) => {
      setSkillDraft({ id: Date.now(), text: skillTestPrompts[lang][skill] });
      if (window.innerWidth < 800) {
        setLeftSidebarOpen(false);
      }
    },
    [lang],
  );

  const handleSelectProject = useCallback((projectId: string) => {
    setWorkspace((prev) => {
      const project = prev.projects[projectId];
      if (!project) return prev;
      const nextConversationId = project.conversationIds.find((id) => prev.conversations[id]) || prev.activeConversationId;
      return { ...prev, activeProjectId: projectId, activeConversationId: nextConversationId };
    });
    clearHashAnchor();
    if (window.innerWidth < 800) setLeftSidebarOpen(false);
  }, []);

  const handleSelectConversation = useCallback((conversationId: string) => {
    setWorkspace((prev) => {
      const conversation = prev.conversations[conversationId];
      if (!conversation) return prev;
      return {
        ...prev,
        activeProjectId: conversation.projectId,
        activeConversationId: conversationId,
      };
    });
    clearHashAnchor();
    if (window.innerWidth < 800) setLeftSidebarOpen(false);
  }, []);

  const handleCreateProject = useCallback(() => {
    setWorkspace((prev) => {
      const now = Date.now();
      const projectNumber = Object.keys(prev.projects).length + 1;
      const projectId = createId('project');
      const conversation = createConversation(projectId);
      const project: ChatProject = {
        id: projectId,
        title: `Project ${projectNumber}`,
        conversationIds: [conversation.id],
        createdAt: now,
        updatedAt: now,
      };
      return {
        projects: { ...prev.projects, [project.id]: project },
        conversations: { ...prev.conversations, [conversation.id]: conversation },
        activeProjectId: project.id,
        activeConversationId: conversation.id,
      };
    });
    clearHashAnchor();
  }, []);

  const handleCreateConversation = useCallback(() => {
    setWorkspace((prev) => {
      const project = prev.projects[prev.activeProjectId];
      if (!project) return prev;
      const now = Date.now();
      const conversation = createConversation(project.id);
      return {
        ...prev,
        projects: {
          ...prev.projects,
          [project.id]: {
            ...project,
            conversationIds: [conversation.id, ...project.conversationIds],
            updatedAt: now,
          },
        },
        conversations: { ...prev.conversations, [conversation.id]: conversation },
        activeConversationId: conversation.id,
      };
    });
    clearHashAnchor();
  }, []);

  const handleDeleteConversation = useCallback(
    (conversationId: string) => {
      if (runningConversationIds.includes(conversationId)) return;
      setWorkspace((prev) => {
        const conversation = prev.conversations[conversationId];
        if (!conversation) return prev;
        const project = prev.projects[conversation.projectId];
        if (!project) return prev;

        const remainingConversationIds = project.conversationIds.filter((id) => id !== conversationId);
        const nextConversations = { ...prev.conversations };
        delete nextConversations[conversationId];

        let nextProject = { ...project, conversationIds: remainingConversationIds, updatedAt: Date.now() };
        let nextActiveProjectId = prev.activeProjectId;
        let nextActiveConversationId = prev.activeConversationId;

        if (remainingConversationIds.length === 0) {
          const replacement = createConversation(project.id);
          nextConversations[replacement.id] = replacement;
          nextProject = { ...nextProject, conversationIds: [replacement.id] };
          if (prev.activeConversationId === conversationId) nextActiveConversationId = replacement.id;
        } else if (prev.activeConversationId === conversationId) {
          nextActiveConversationId = remainingConversationIds.find((id) => nextConversations[id]) || remainingConversationIds[0];
        }

        return {
          ...prev,
          projects: { ...prev.projects, [project.id]: nextProject },
          conversations: nextConversations,
          activeProjectId: nextActiveProjectId,
          activeConversationId: nextActiveConversationId,
        };
      });
      setTaskQueue((prev) => prev.filter((task) => task.conversationId !== conversationId));
      clearHashAnchor();
    },
    [runningConversationIds],
  );

  const handleDeleteProject = useCallback(
    (projectId: string) => {
      setWorkspace((prev) => {
        const project = prev.projects[projectId];
        if (!project) return prev;
        if (project.conversationIds.some((id) => runningConversationIds.includes(id))) return prev;

        const nextProjects = { ...prev.projects };
        const nextConversations = { ...prev.conversations };
        delete nextProjects[projectId];
        project.conversationIds.forEach((id) => delete nextConversations[id]);

        let nextActiveProjectId = prev.activeProjectId;
        let nextActiveConversationId = prev.activeConversationId;

        const remainingProjects = Object.values(nextProjects).sort((a, b) => b.updatedAt - a.updatedAt);
        if (remainingProjects.length === 0) {
          const replacementState = createWorkspaceState();
          return replacementState;
        }

        if (prev.activeProjectId === projectId) {
          const replacementProject = remainingProjects[0];
          nextActiveProjectId = replacementProject.id;
          nextActiveConversationId = replacementProject.conversationIds.find((id) => nextConversations[id]) || nextActiveConversationId;
        } else if (!nextConversations[nextActiveConversationId]) {
          const activeProjectAfterDelete = nextProjects[nextActiveProjectId] || remainingProjects[0];
          nextActiveProjectId = activeProjectAfterDelete.id;
          nextActiveConversationId = activeProjectAfterDelete.conversationIds.find((id) => nextConversations[id]) || nextActiveConversationId;
        }

        return {
          projects: nextProjects,
          conversations: nextConversations,
          activeProjectId: nextActiveProjectId,
          activeConversationId: nextActiveConversationId,
        };
      });
      setTaskQueue((prev) => {
        const project = workspace.projects[projectId];
        if (!project) return prev;
        const conversationIds = new Set(project.conversationIds);
        return prev.filter((task) => !conversationIds.has(task.conversationId));
      });
      clearHashAnchor();
    },
    [runningConversationIds, workspace.projects],
  );

  const handleClearActiveHistory = useCallback(() => {
    clearHashAnchor();
    updateActiveMessages([]);
    localStorage.removeItem(HISTORY_KEY);
    fetch(`${API_BASE}/conversations/clear`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: activeSessionId }),
    }).catch(() => {
      // Local history is already cleared; backend may be offline.
    });
  }, [activeSessionId, updateActiveMessages]);

  const handleClearAllData = useCallback(async () => {
    if (runningConversationIds.length > 0) return;
    const message =
      lang === 'zh'
        ? '确认清空所有前端对话、后端对话历史和长期记忆？这个操作不可撤销。'
        : 'Clear all frontend conversations, backend conversation history, and long-term memory? This cannot be undone.';
    if (!window.confirm(message)) return;

    clearHashAnchor();
    const emptyWorkspace = createWorkspaceState();
    setWorkspace(emptyWorkspace);
    setTaskQueue([]);
    localStorage.removeItem(HISTORY_KEY);
    localStorage.removeItem(TASK_QUEUE_KEY);
    localStorage.setItem(WORKSPACE_KEY, JSON.stringify(emptyWorkspace));
    setDataActionMessage(lang === 'zh' ? '正在清理数据...' : 'Clearing data...');

    try {
      const [conversationRes, memoryRes] = await Promise.all([
        fetch(`${API_BASE}/conversations/clear`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({}) }),
        fetch(`${API_BASE}/memory/clear`, { method: 'POST' }),
      ]);
      if (!conversationRes.ok || !memoryRes.ok) throw new Error('Backend cleanup failed');
      setDataActionMessage(lang === 'zh' ? '已清空对话和记忆' : 'Chats and memory cleared');
    } catch {
      setDataActionMessage(lang === 'zh' ? '前端已清空，后端清理失败或未启动' : 'Frontend cleared; backend cleanup failed or is offline');
    }
  }, [lang, runningConversationIds.length]);

  useEffect(() => {
    const syncHashTarget = () => {
      const anchorId = getHashAnchor();
      if (!anchorId) return;
      window.setTimeout(() => scrollToAnchor(anchorId, false), 80);
    };
    syncHashTarget();
    window.addEventListener('hashchange', syncHashTarget);
    return () => window.removeEventListener('hashchange', syncHashTarget);
  }, [messages]);

  useEffect(() => {
    if (conversationNavItems.length === 0) {
      setActiveAnchorId('');
      return;
    }

    const updateActiveAnchor = () => {
      const visibleItems = conversationNavItems
        .map((item) => document.getElementById(item.anchorId))
        .filter((element): element is HTMLElement => Boolean(element));
      if (visibleItems.length === 0) return;

      const topOffset = 96;
      const current =
        [...visibleItems]
          .reverse()
          .find((element) => element.getBoundingClientRect().top <= topOffset) || visibleItems[0];
      setActiveAnchorId(current.id);
    };

    const scrollContainer = document.querySelector('[data-chat-scroll-container]');
    const timeout = window.setTimeout(updateActiveAnchor, 120);
    scrollContainer?.addEventListener('scroll', updateActiveAnchor, { passive: true });
    window.addEventListener('resize', updateActiveAnchor);

    return () => {
      window.clearTimeout(timeout);
      scrollContainer?.removeEventListener('scroll', updateActiveAnchor);
      window.removeEventListener('resize', updateActiveAnchor);
    };
  }, [conversationNavItems]);

  const handleSendMessage = async (text: string) => {
    const cleanText = text.trim();
    if (!cleanText || activeChatLoading) return;
    clearHashAnchor();

    const targetConversationId = workspace.activeConversationId;
    const targetConversation = workspace.conversations[targetConversationId];
    if (!targetConversation) return;
    const sender = targetConversation.members.find((member) => member.id === targetConversation.activeUserId) || targetConversation.members[0] || activeMember;
    const targetSessionId = getConversationSessionId(targetConversation);
    const userMsgId = `${Date.now()}-user`;
    const botMsgId = `${Date.now()}-assistant`;
    const taskId = createId('task');
    const now = Date.now();
    updateConversationMessages(targetConversationId, (prev) => [
      ...prev,
      { id: userMsgId, role: 'user', content: cleanText, userId: sender.id, userName: sender.name },
      { id: botMsgId, role: 'assistant', content: '', thoughts: initialProgressThoughts(lang), isStreaming: true },
    ]);
    const nextTask: UserTask = {
      id: taskId,
      title: summarizeMessage(cleanText, lang === 'zh' ? '新任务' : 'New task'),
      status: 'active',
      conversationId: targetConversationId,
      messageId: userMsgId,
      createdAt: now,
      updatedAt: now,
    };
    setTaskQueue((prev) => [nextTask, ...prev].slice(0, 80));
    setRunningConversationIds((prev) => (prev.includes(targetConversationId) ? prev : [...prev, targetConversationId]));

    try {
      const res = await fetch(`${API_BASE}/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: targetSessionId,
          room_id: targetConversation.roomId,
          user_id: sender.id,
          user_name: sender.name,
          message: cleanText,
          domain: 'auto',
          provider: modelSelection.provider,
          model: modelSelection.model,
          lang,
        }),
      });

      const contentType = res.headers.get('content-type') || '';
      if (contentType.includes('text/event-stream')) {
        await streamEventResponse(res, botMsgId, (updater) => updateConversationMessages(targetConversationId, updater));
      } else {
        const data = await res.json();
        const stagedThoughts =
          lang === 'zh'
            ? ['后端已返回结果，正在整理回复']
            : ['Backend returned a result, preparing the response'];
        for (const thought of stagedThoughts) {
          updateConversationMessages(targetConversationId, (prev) => prev.map((m) => (m.id === botMsgId ? appendMessageThought(m, thought) : m)));
          await delay(220);
        }
        await typeAnswer(data.answer || (lang === 'zh' ? '后端返回为空。' : 'The backend returned an empty answer.'), botMsgId, (updater) =>
          updateConversationMessages(targetConversationId, updater),
        );
      }
    } catch {
      updateConversationMessages(targetConversationId, (prev) =>
        prev.map((m) =>
          m.id === botMsgId
            ? {
                ...m,
                content: lang === 'zh' ? '后端暂时不可用。请确认 start.bat 已启动后端服务。' : 'The backend is not reachable. Please make sure the backend is running from start.bat.',
              }
            : m,
        ),
      );
    } finally {
      setTaskQueue((prev) =>
        prev.map((task) => (task.id === taskId ? { ...task, status: 'completed', updatedAt: Date.now() } : task)),
      );
      updateConversationMessages(targetConversationId, (prev) => prev.map((m) => (m.id === botMsgId ? { ...m, isStreaming: false } : m)));
      setRunningConversationIds((prev) => prev.filter((id) => id !== targetConversationId));
    }
  };

  const handleConfirmAction = async (action: 'approve' | 'deny') => {
    try {
      await fetch(`${API_BASE}/submit_confirmation`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: activeSessionId, request_id: confirmReq?.request_id, action }),
      });
    } finally {
      setConfirmReq(null);
    }
  };

  if (loading) {
    return (
      <div className="grid h-screen place-items-center bg-[var(--bg-app)] text-[var(--text-main)]">
        <div className="h-9 w-9 animate-spin rounded-full border-2 border-[var(--accent)] border-t-transparent" />
      </div>
    );
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-[var(--bg-app)] text-[var(--text-main)]">
      {leftSidebarOpen && <button aria-label="Close menu overlay" className="fixed inset-0 z-30 bg-black/35 md:hidden" onClick={() => setLeftSidebarOpen(false)} />}
      <aside className={`fixed inset-y-0 left-0 z-40 transition-transform duration-200 md:relative md:translate-x-0 ${leftSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
        <Sidebar
          t={t}
          lang={lang}
          projects={projects}
          conversations={projectConversations}
          activeProjectId={workspace.activeProjectId}
          activeConversationId={workspace.activeConversationId}
          runningConversationIds={runningConversationIds}
          activeTasks={activeTasks}
          completedTasks={completedTasks}
          onSelectProject={handleSelectProject}
          onCreateProject={handleCreateProject}
          onDeleteProject={handleDeleteProject}
          onSelectConversation={handleSelectConversation}
          onCreateConversation={handleCreateConversation}
          onDeleteConversation={handleDeleteConversation}
          onSelectTask={handleSelectTask}
          onDeleteTask={handleDeleteTask}
          onTestSkill={handleTestSkill}
          onOpenEvolutionManager={() => setEvolutionManagerOpen(true)}
          onOpenMemoryManager={() => setMemoryManagerOpen(true)}
          onOpenSkillEditor={() => setSkillEditorOpen(true)}
          onClearAllData={handleClearAllData}
          clearAllDisabled={runningConversationIds.length > 0}
          dataActionMessage={dataActionMessage}
          onClose={() => setLeftSidebarOpen(false)}
        />
      </aside>

      <MainChat
        t={t}
        lang={lang}
        messages={messages}
        activeProjectTitle={displayWorkspaceTitle(activeProject?.title || DEFAULT_PROJECT_TITLE, lang)}
        activeConversationTitle={displayWorkspaceTitle(activeConversation?.title || DEFAULT_CONVERSATION_TITLE, lang)}
        members={activeMembers}
        activeMemberId={activeMember.id}
        isChatLoading={activeChatLoading}
        skillDraft={skillDraft}
        modelProviders={modelProviders}
        modelSelection={modelSelection}
        onSelectModel={setModelSelection}
        onSelectMember={handleSelectActiveMember}
        onAddMember={handleAddActiveMember}
        onPrompt={handleSendMessage}
        onSendMessage={handleSendMessage}
        onClearHistory={handleClearActiveHistory}
        onToggleLang={() => setLang((current) => (current === 'zh' ? 'en' : 'zh'))}
        onToggleLeft={() => setLeftSidebarOpen((current) => !current)}
        onToggleRight={() => setRightPanelOpen((current) => !current)}
      />

      {rightPanelOpen && <button aria-label="Close monitor overlay" className="fixed inset-0 z-30 bg-black/35 xl:hidden" onClick={() => setRightPanelOpen(false)} />}
      <aside className={`fixed inset-y-0 right-0 z-40 transition-transform duration-200 xl:relative ${rightPanelOpen ? 'translate-x-0' : 'translate-x-full xl:hidden'}`}>
        <RuntimePanel
          t={t}
          lang={lang}
          navItems={conversationNavItems}
          activeAnchorId={activeAnchorId}
          onNavigate={handleNavigateToAnchor}
          onClose={() => setRightPanelOpen(false)}
        />
      </aside>

      {confirmReq && <ConfirmModal lang={lang} req={confirmReq} onAction={handleConfirmAction} />}
      {skillEditorOpen && <SkillEditorModal lang={lang} onClose={() => setSkillEditorOpen(false)} />}
      {evolutionManagerOpen && <EvolutionManagerModal lang={lang} onClose={() => setEvolutionManagerOpen(false)} />}
      {memoryManagerOpen && <MemoryManagerModal lang={lang} onClose={() => setMemoryManagerOpen(false)} />}
    </div>
  );
}

async function streamEventResponse(
  res: Response,
  botMsgId: string,
  setMessages: React.Dispatch<React.SetStateAction<Message[]>>,
) {
  const reader = res.body?.getReader();
  if (!reader) return;
  const decoder = new TextDecoder('utf-8');
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() || '';
    for (const line of lines) {
      if (!line.startsWith('data: ')) continue;
      const dataStr = line.substring(6).trim();
      if (!dataStr || dataStr === '[DONE]') continue;
      try {
        const data = JSON.parse(dataStr);
        setMessages((prev) =>
          prev.map((m) => {
            if (m.id !== botMsgId) return m;
            if (data.type === 'thought' || data.type === 'tool') {
              return { ...m, thoughts: [...(m.thoughts || []), String(data.content || '')] };
            }
            if (data.type === 'token') {
              return { ...m, content: m.content + String(data.content || '') };
            }
            return m;
          }),
        );
      } catch {
        // Ignore malformed event fragments.
      }
    }
  }
}

async function typeAnswer(answerText: string, botMsgId: string, setMessages: React.Dispatch<React.SetStateAction<Message[]>>) {
  let currentText = '';
  for (const char of answerText.split('')) {
    currentText += char;
    setMessages((prev) => prev.map((m) => (m.id === botMsgId ? { ...m, content: currentText } : m)));
    await delay(8);
  }
}

function delay(ms: number) {
  return new Promise((resolve) => window.setTimeout(resolve, ms));
}

function Sidebar({
  t,
  lang,
  projects,
  conversations,
  activeProjectId,
  activeConversationId,
  runningConversationIds,
  activeTasks,
  completedTasks,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onSelectTask,
  onDeleteTask,
  onTestSkill,
  onOpenEvolutionManager,
  onOpenMemoryManager,
  onOpenSkillEditor,
  onClearAllData,
  clearAllDisabled,
  dataActionMessage,
  onClose,
}: {
  t: ConfigText;
  lang: Lang;
  projects: ChatProject[];
  conversations: ChatConversation[];
  activeProjectId: string;
  activeConversationId: string;
  runningConversationIds: string[];
  activeTasks: UserTask[];
  completedTasks: UserTask[];
  onSelectProject: (projectId: string) => void;
  onCreateProject: () => void;
  onDeleteProject: (projectId: string) => void;
  onSelectConversation: (conversationId: string) => void;
  onCreateConversation: () => void;
  onDeleteConversation: (conversationId: string) => void;
  onSelectTask: (task: UserTask) => void;
  onDeleteTask: (taskId: string) => void;
  onTestSkill: (skill: SkillKey) => void;
  onOpenEvolutionManager: () => void;
  onOpenMemoryManager: () => void;
  onOpenSkillEditor: () => void;
  onClearAllData: () => void;
  clearAllDisabled: boolean;
  dataActionMessage: string;
  onClose: () => void;
}) {
  return (
    <div className="flex h-full w-[280px] flex-col bg-[var(--bg-rail)] text-white shadow-2xl">
      <div className="flex h-16 items-center justify-between border-b border-white/10 px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-white text-[var(--bg-rail)]">
            <Brain className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{t.hubTitle}</div>
            <div className="truncate text-xs text-white/55">{t.hubSub}</div>
          </div>
        </div>
        <button className="rounded-lg p-2 text-white/60 hover:bg-white/10 hover:text-white md:hidden" onClick={onClose} aria-label="Close sidebar">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto px-3 py-4">
        <div className="mb-4">
          <PanelHeader label={lang === 'zh' ? '项目' : 'Projects'} onAdd={onCreateProject} />
          <div className="space-y-1.5">
            {projects.map((project) => (
              <SidebarButton
                key={project.id}
                active={project.id === activeProjectId}
                icon={<Folder className="h-4 w-4" />}
                primary={displayWorkspaceTitle(project.title, lang)}
                secondary={
                  project.conversationIds.some((id) => runningConversationIds.includes(id))
                    ? lang === 'zh'
                      ? '回答中'
                      : 'Answering'
                    : `${project.conversationIds.length} ${lang === 'zh' ? '个对话' : 'chats'}`
                }
                deleteTitle={lang === 'zh' ? '删除项目' : 'Delete project'}
                deleteDisabled={project.conversationIds.some((id) => runningConversationIds.includes(id))}
                onClick={() => onSelectProject(project.id)}
                onDelete={() => onDeleteProject(project.id)}
              />
            ))}
          </div>
        </div>

        <div className="mb-6">
          <PanelHeader label={lang === 'zh' ? '对话' : 'Conversations'} onAdd={onCreateConversation} />
          <div className="space-y-1.5">
            {conversations.map((conversation) => (
              <SidebarButton
                key={conversation.id}
                active={conversation.id === activeConversationId}
                icon={<MessageSquare className="h-4 w-4" />}
                primary={displayWorkspaceTitle(conversation.title, lang)}
                secondary={
                  runningConversationIds.includes(conversation.id)
                    ? lang === 'zh'
                      ? '回答中'
                      : 'Answering'
                    : conversation.messages.length
                      ? `${conversation.messages.length} ${lang === 'zh' ? '条消息' : 'messages'}`
                      : lang === 'zh'
                        ? '空对话'
                        : 'Empty chat'
                }
                deleteTitle={lang === 'zh' ? '删除对话' : 'Delete conversation'}
                deleteDisabled={runningConversationIds.includes(conversation.id)}
                onClick={() => onSelectConversation(conversation.id)}
                onDelete={() => onDeleteConversation(conversation.id)}
              />
            ))}
          </div>
        </div>

        <SectionLabel>{t.activeTasks}</SectionLabel>
        <div className="space-y-1.5">
          {activeTasks.length ? (
            activeTasks.map((task) => <TaskQueueItem key={task.id} task={task} active lang={lang} onSelect={onSelectTask} onDelete={onDeleteTask} />)
          ) : (
            <EmptyTaskQueueText>{lang === 'zh' ? '暂无正在推进的任务' : 'No active tasks'}</EmptyTaskQueueText>
          )}
        </div>
        <div className="mt-6">
          <SectionLabel>{t.completedTasks}</SectionLabel>
          <div className="space-y-1.5">
            {completedTasks.length ? (
              completedTasks.map((task) => <TaskQueueItem key={task.id} task={task} lang={lang} onSelect={onSelectTask} onDelete={onDeleteTask} />)
            ) : (
              <EmptyTaskQueueText>{lang === 'zh' ? '暂无已完成任务' : 'No completed tasks'}</EmptyTaskQueueText>
            )}
          </div>
        </div>
        <div className="mt-6 rounded-lg border border-white/10 bg-white/[0.04] p-3">
          <div className="mb-3 flex items-center justify-between text-xs text-white/65">
            <span>{lang === 'zh' ? '技能' : 'Skills'}</span>
            <ChevronDown className="h-3.5 w-3.5" />
          </div>
          <div className="grid grid-cols-2 gap-2">
            <SkillChip icon={<FileText className="h-3.5 w-3.5" />} label={lang === 'zh' ? '科研' : 'Research'} onClick={() => onTestSkill('research')} />
            <SkillChip icon={<Code2 className="h-3.5 w-3.5" />} label={lang === 'zh' ? '代码' : 'Code'} onClick={() => onTestSkill('code')} />
            <SkillChip icon={<Clapperboard className="h-3.5 w-3.5" />} label={lang === 'zh' ? '媒体/视频' : 'Media/Video'} onClick={() => onTestSkill('mediaVideo')} />
            <SkillChip icon={<Activity className="h-3.5 w-3.5" />} label={lang === 'zh' ? '监控' : 'Watch'} onClick={() => onTestSkill('watch')} />
          </div>
        </div>
      </div>

        <div className="mt-6 rounded-lg border border-white/10 bg-white/[0.04] p-3">
          <div className="mb-3 flex items-center justify-between text-xs text-white/65">
            <span>{lang === 'zh' ? '数据' : 'Data'}</span>
            <Database className="h-3.5 w-3.5" />
          </div>
          <div className="space-y-2">
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-black/10 px-3 py-2 text-left text-xs text-white/75 transition hover:border-[var(--mint)] hover:bg-white/10 hover:text-white"
              onClick={onOpenEvolutionManager}
            >
              <ShieldCheck className="h-3.5 w-3.5" />
              <span className="min-w-0 truncate">{lang === 'zh' ? '进化审查' : 'Evolution review'}</span>
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-black/10 px-3 py-2 text-left text-xs text-white/75 transition hover:border-[var(--mint)] hover:bg-white/10 hover:text-white"
              onClick={onOpenMemoryManager}
            >
              <Database className="h-3.5 w-3.5" />
              <span className="min-w-0 truncate">{lang === 'zh' ? '管理长期记忆' : 'Manage memory'}</span>
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-black/10 px-3 py-2 text-left text-xs text-white/75 transition hover:border-[var(--mint)] hover:bg-white/10 hover:text-white"
              onClick={onOpenSkillEditor}
            >
              <Pencil className="h-3.5 w-3.5" />
              <span className="min-w-0 truncate">{lang === 'zh' ? '编辑技能' : 'Edit skills'}</span>
            </button>
            <button
              type="button"
              className="flex w-full items-center gap-2 rounded-lg border border-white/10 bg-black/10 px-3 py-2 text-left text-xs text-white/75 transition hover:border-[var(--danger)] hover:bg-white/10 hover:text-white disabled:cursor-not-allowed disabled:opacity-40"
              onClick={onClearAllData}
              disabled={clearAllDisabled}
              title={clearAllDisabled ? (lang === 'zh' ? '回答生成中不能清空' : 'Cannot clear while answering') : undefined}
            >
              <Eraser className="h-3.5 w-3.5" />
              <span className="min-w-0 truncate">{lang === 'zh' ? '清空对话和记忆' : 'Clear chats + memory'}</span>
            </button>
          </div>
          {dataActionMessage ? <div className="mt-2 break-words text-xs leading-5 text-white/45">{dataActionMessage}</div> : null}
        </div>

      <div className="border-t border-white/10 p-4">
        <div className="flex items-center gap-3 rounded-lg bg-white/[0.04] px-3 py-2">
          <span className="h-2 w-2 rounded-full bg-[var(--ok)]" />
          <div className="min-w-0 text-xs text-white/65">
            {t.connectedTo} <span className="font-mono text-white/85">{API_BASE.replace('http://', '')}</span>
          </div>
        </div>
      </div>
    </div>
  );
}

function PanelHeader({ label, onAdd }: { label: string; onAdd: () => void }) {
  return (
    <div className="mb-2 flex items-center justify-between px-2 text-xs font-semibold text-white/45">
      <span>{label}</span>
      <button className="grid h-6 w-6 place-items-center rounded-md text-white/55 hover:bg-white/10 hover:text-white" type="button" onClick={onAdd} title={label}>
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function SidebarButton({
  active,
  icon,
  primary,
  secondary,
  deleteTitle,
  deleteDisabled,
  onClick,
  onDelete,
}: {
  active: boolean;
  icon: React.ReactNode;
  primary: string;
  secondary: string;
  deleteTitle: string;
  deleteDisabled?: boolean;
  onClick: () => void;
  onDelete: () => void;
}) {
  return (
    <div
      className={`group flex w-full items-center gap-1 rounded-lg border pr-1 transition ${
        active ? 'border-white/14 bg-white/10 text-white' : 'border-transparent text-white/60 hover:bg-white/[0.06] hover:text-white'
      }`}
    >
      <button type="button" className="flex min-w-0 flex-1 items-center gap-3 px-3 py-2.5 text-left text-sm" onClick={onClick}>
        <span className={active ? 'text-[var(--mint)]' : 'text-white/45'}>{icon}</span>
        <span className="min-w-0 flex-1">
          <span className="block truncate">{primary}</span>
          <span className="block truncate text-xs text-white/40">{secondary}</span>
        </span>
      </button>
      <button
        type="button"
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-white/45 transition hover:bg-white/10 hover:text-[var(--danger-soft)] disabled:cursor-not-allowed disabled:opacity-20"
        title={deleteDisabled ? `${deleteTitle} disabled while answering` : deleteTitle}
        disabled={deleteDisabled}
        onClick={onDelete}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="mb-2 px-2 text-xs font-semibold text-white/45">{children}</div>;
}

function TaskQueueItem({
  task,
  active,
  lang,
  onSelect,
  onDelete,
}: {
  task: UserTask;
  active?: boolean;
  lang: Lang;
  onSelect: (task: UserTask) => void;
  onDelete: (taskId: string) => void;
}) {
  return (
    <div className={`group flex items-center gap-1 rounded-lg border pr-1 transition ${active ? 'border-white/14 bg-white/10 text-white' : 'border-transparent text-white/62 hover:bg-white/[0.06] hover:text-white'}`}>
      <button type="button" className="flex min-w-0 flex-1 items-start gap-2 px-3 py-2.5 text-left" onClick={() => onSelect(task)} title={task.title}>
        <span className={`mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md ${active ? 'bg-[var(--mint)]/15 text-[var(--mint)]' : 'bg-white/8 text-white/45'}`}>
          {active ? <Activity className="h-3.5 w-3.5" /> : <CheckCircle2 className="h-3.5 w-3.5" />}
        </span>
        <span className="min-w-0 flex-1">
          <span className="line-clamp-2 text-xs leading-5">{task.title}</span>
          <span className="mt-0.5 block truncate text-[10px] text-white/35">
            {active ? (lang === 'zh' ? '进行中' : 'In progress') : lang === 'zh' ? '已完成' : 'Completed'} · {formatRelativeTaskTime(task.updatedAt, lang)}
          </span>
        </span>
      </button>
      <button
        type="button"
        className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-white/35 opacity-0 transition hover:bg-white/10 hover:text-[var(--danger)] group-hover:opacity-100"
        title={lang === 'zh' ? '删除任务' : 'Delete task'}
        onClick={(event) => {
          event.stopPropagation();
          onDelete(task.id);
        }}
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function EmptyTaskQueueText({ children }: { children: React.ReactNode }) {
  return <div className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-white/35">{children}</div>;
}

function formatRelativeTaskTime(timestamp: number, lang: Lang) {
  const diff = Math.max(0, Date.now() - timestamp);
  const minutes = Math.floor(diff / 60000);
  if (minutes < 1) return lang === 'zh' ? '刚刚' : 'just now';
  if (minutes < 60) return lang === 'zh' ? `${minutes} 分钟前` : `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return lang === 'zh' ? `${hours} 小时前` : `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return lang === 'zh' ? `${days} 天前` : `${days}d ago`;
}

function NavItem({ active, icon, text }: { active?: boolean; icon: React.ReactNode; text: string }) {
  return (
    <div className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm ${active ? 'border-white/14 bg-white/10 text-white' : 'border-transparent text-white/60 hover:bg-white/[0.06] hover:text-white'}`}>
      <span className={active ? 'text-[var(--mint)]' : 'text-white/45'}>{icon}</span>
      <span className="min-w-0 truncate">{text}</span>
    </div>
  );
}

function SkillChip({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
  return (
    <button
      type="button"
      className="flex min-w-0 items-center gap-1.5 rounded-lg border border-white/10 bg-black/10 px-2 py-1.5 text-left text-xs text-white/70 transition hover:border-[var(--mint)] hover:bg-white/10 hover:text-white"
      onClick={onClick}
      title={label}
    >
      {icon}
      <span className="truncate">{label}</span>
    </button>
  );
}

function MainChat({
  t,
  lang,
  messages,
  activeProjectTitle,
  activeConversationTitle,
  members,
  activeMemberId,
  isChatLoading,
  skillDraft,
  modelProviders,
  modelSelection,
  onSelectModel,
  onSelectMember,
  onAddMember,
  onPrompt,
  onSendMessage,
  onClearHistory,
  onToggleLang,
  onToggleLeft,
  onToggleRight,
}: {
  t: ConfigText;
  lang: Lang;
  messages: Message[];
  activeProjectTitle: string;
  activeConversationTitle: string;
  members: ChatMember[];
  activeMemberId: string;
  isChatLoading: boolean;
  skillDraft: SkillDraft | null;
  modelProviders: ModelProviderOption[];
  modelSelection: ModelSelection;
  onSelectModel: (selection: ModelSelection) => void;
  onSelectMember: (memberId: string) => void;
  onAddMember: () => void;
  onPrompt: (value: string) => void;
  onSendMessage: (value: string) => void;
  onClearHistory: () => void;
  onToggleLang: () => void;
  onToggleLeft: () => void;
  onToggleRight: () => void;
}) {
  const [inputValue, setInputValue] = useState('');
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    window.setTimeout(() => {
      const anchorId = getHashAnchor();
      if (anchorId && document.getElementById(anchorId)) return;
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' });
    }, 30);
  }, [messages, isChatLoading]);

  useEffect(() => {
    if (!skillDraft) return;
    setInputValue(skillDraft.text);
    window.setTimeout(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(skillDraft.text.length, skillDraft.text.length);
    }, 0);
  }, [skillDraft]);

  const submit = () => {
    if (!inputValue.trim() || isChatLoading) return;
    onSendMessage(inputValue);
    setInputValue('');
  };

  return (
    <main className="flex min-w-0 flex-1 flex-col">
      <header className="flex h-16 shrink-0 items-center justify-between border-b border-[var(--border)] bg-[var(--bg-panel)] px-3 sm:px-4">
        <div className="flex min-w-0 flex-1 items-center gap-2 sm:gap-3">
          <button data-testid="sidebar-toggle" className="rounded-lg p-2 text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)] md:hidden" onClick={onToggleLeft} aria-label="Open sidebar">
            <Menu className="h-5 w-5" />
          </button>
          <div className="hidden h-9 w-9 place-items-center rounded-lg bg-[var(--bg-soft)] text-[var(--accent)] sm:grid">
            <LayoutDashboard className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{lang === 'zh' ? '智能体控制台' : 'Agent Console'}</div>
            <div className="truncate text-xs text-[var(--text-muted)]">
              {activeProjectTitle} / {activeConversationTitle}
            </div>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-1 sm:gap-2">
          <ChatSearch messages={chatSearchMessages} onJumpToMessage={(id) => setChatSearchScrollTo(id)} />
          <MemberPicker lang={lang} members={members} activeMemberId={activeMemberId} onSelect={onSelectMember} onAdd={onAddMember} />
          <ModelPicker lang={lang} providers={modelProviders} selection={modelSelection} onSelect={onSelectModel} />
          <IconButton testId="clear-history" title={lang === 'zh' ? '清空历史' : 'Clear history'} onClick={onClearHistory}>
            <Trash2 className="h-4 w-4" />
          </IconButton>
          <IconButton testId="lang-toggle" title={lang === 'zh' ? '切换语言' : 'Switch language'} onClick={onToggleLang}>
            <Languages className="h-4 w-4" />
          </IconButton>
          <IconButton testId="runtime-toggle" title={lang === 'zh' ? '运行状态' : 'Runtime'} onClick={onToggleRight}>
            <PanelRightClose className="h-4 w-4" />
          </IconButton>
        </div>
      </header>

      <div data-chat-scroll-container className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto flex max-w-4xl flex-col gap-5">
          {messages.length === 0 ? (
            <EmptyChat lang={lang} onPrompt={onPrompt} />
          ) : (
            messages.map((msg) =>
              msg.role === 'user' ? (
                <UserMessage key={msg.id} anchorId={getMessageAnchorId(msg.id)} content={msg.content} userName={msg.userName} />
              ) : (
                <AgentMessage key={msg.id} lang={lang} anchorId={getMessageAnchorId(msg.id)} traceAnchorId={getTraceAnchorId(msg.id)} thoughts={msg.thoughts} isStreaming={msg.isStreaming}>
                  {msg.content}
                </AgentMessage>
              ),
            )
          )}
          <div ref={messagesEndRef} className="h-1" />
        </div>
      </div>

      <div className="shrink-0 border-t border-[var(--border)] bg-[var(--bg-panel)] px-4 py-4 md:px-8">
        <div className="mx-auto max-w-4xl">
          <div className="flex items-end gap-3 rounded-lg border border-[var(--border-strong)] bg-white p-2 shadow-sm">
            <textarea
              ref={textareaRef}
              value={inputValue}
              onChange={(event) => setInputValue(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault();
                  submit();
                }
              }}
              placeholder={t.inputPlace}
              rows={1}
              disabled={isChatLoading}
              className="max-h-32 min-h-11 flex-1 resize-none bg-transparent px-2 py-2.5 text-sm leading-6 outline-none placeholder:text-[var(--text-muted)]"
            />
            <button className="grid h-11 w-11 shrink-0 place-items-center rounded-lg bg-[var(--accent)] text-white transition hover:bg-[var(--accent-strong)] disabled:cursor-not-allowed disabled:bg-[var(--disabled)]" disabled={isChatLoading || !inputValue.trim()} onClick={submit} title={lang === 'zh' ? '发送' : 'Send'}>
              <Send className="h-4 w-4" />
            </button>
          </div>
          <div className="mt-2 text-center text-xs text-[var(--text-muted)]">{t.inputHint}</div>
        </div>
      </div>
    </main>
  );
}

function MemberPicker({
  lang,
  members,
  activeMemberId,
  onSelect,
  onAdd,
}: {
  lang: Lang;
  members: ChatMember[];
  activeMemberId: string;
  onSelect: (memberId: string) => void;
  onAdd: () => void;
}) {
  const [open, setOpen] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);
  const activeMember = members.find((member) => member.id === activeMemberId) || members[0];

  useEffect(() => {
    if (!open) return;
    const handlePointer = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    return () => document.removeEventListener('mousedown', handlePointer);
  }, [open]);

  return (
    <div ref={pickerRef} className="relative">
      <button
        type="button"
        className="flex h-9 max-w-[150px] items-center gap-2 rounded-lg border border-[var(--border)] bg-white px-2.5 text-left text-xs text-[var(--text-main)] transition hover:border-[var(--border-strong)]"
        title={lang === 'zh' ? '当前发言人' : 'Current speaker'}
        onClick={() => setOpen((current) => !current)}
      >
        <span className="grid h-5 w-5 shrink-0 place-items-center rounded-md text-white" style={{ backgroundColor: activeMember?.color || MEMBER_COLORS[0] }}>
          <Users className="h-3.5 w-3.5" />
        </span>
        <span className="min-w-0 truncate font-semibold">{activeMember?.name || (lang === 'zh' ? '成员' : 'Member')}</span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-[var(--text-muted)] transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-50 w-[240px] rounded-lg border border-[var(--border)] bg-white p-2 shadow-2xl">
          <div className="mb-2 flex items-center justify-between px-1 text-xs font-semibold text-[var(--text-muted)]">
            <span>{lang === 'zh' ? '群聊成员' : 'Group members'}</span>
            <span>{members.length}</span>
          </div>
          <div className="space-y-1">
            {members.map((member) => {
              const selected = member.id === activeMemberId;
              return (
                <button
                  key={member.id}
                  type="button"
                  className={`flex w-full items-center justify-between gap-2 rounded-lg px-2 py-2 text-left text-sm transition ${selected ? 'bg-[var(--bg-soft)] text-[var(--accent)]' : 'hover:bg-[var(--bg-soft)]'}`}
                  onClick={() => {
                    onSelect(member.id);
                    setOpen(false);
                  }}
                >
                  <span className="flex min-w-0 items-center gap-2">
                    <span className="h-2.5 w-2.5 shrink-0 rounded-full" style={{ backgroundColor: member.color }} />
                    <span className="truncate">{member.name}</span>
                  </span>
                  {selected ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : null}
                </button>
              );
            })}
          </div>
          <button
            type="button"
            className="mt-2 flex w-full items-center justify-center gap-2 rounded-lg border border-[var(--border)] px-3 py-2 text-sm hover:bg-[var(--bg-soft)]"
            onClick={() => {
              onAdd();
              setOpen(false);
            }}
          >
            <UserPlus className="h-4 w-4" />
            {lang === 'zh' ? '添加成员' : 'Add member'}
          </button>
        </div>
      )}
    </div>
  );
}

function ModelPicker({
  lang,
  providers,
  selection,
  onSelect,
}: {
  lang: Lang;
  providers: ModelProviderOption[];
  selection: ModelSelection;
  onSelect: (selection: ModelSelection) => void;
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState('');
  const pickerRef = useRef<HTMLDivElement>(null);
  const activeProvider = providers.find((provider) => provider.id === selection.provider) || providers[0];
  const activeModel = activeProvider?.models.find((model) => model.id === selection.model) || activeProvider?.models[0];

  useEffect(() => {
    if (!open) return;
    const handlePointer = (event: MouseEvent) => {
      if (!pickerRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', handlePointer);
    return () => document.removeEventListener('mousedown', handlePointer);
  }, [open]);

  const normalizedQuery = query.trim().toLowerCase();
  const filteredProviders = providers
    .map((provider) => {
      const providerMatches = `${provider.id} ${provider.label}`.toLowerCase().includes(normalizedQuery);
      const models = provider.models.filter((model) => providerMatches || `${model.id} ${model.name || ''}`.toLowerCase().includes(normalizedQuery));
      return { ...provider, models };
    })
    .filter((provider) => provider.models.length > 0);

  return (
    <div ref={pickerRef} className="relative">
      <button
        type="button"
        data-testid="model-picker-toggle"
        className="flex h-9 max-w-[190px] items-center gap-2 rounded-lg border border-[var(--border)] bg-white px-2.5 text-left text-xs text-[var(--text-main)] transition hover:border-[var(--border-strong)]"
        title={lang === 'zh' ? '选择模型' : 'Select model'}
        onClick={() => setOpen((current) => !current)}
      >
        <Brain className="h-4 w-4 shrink-0 text-[var(--accent)]" />
        <span className="min-w-0">
          <span className="block truncate font-semibold">{activeModel?.name || activeModel?.id || selection.model}</span>
          <span className="block truncate text-[10px] text-[var(--text-muted)]">{activeProvider?.label || selection.provider}</span>
        </span>
        <ChevronDown className={`h-3.5 w-3.5 shrink-0 text-[var(--text-muted)] transition ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-50 w-[330px] rounded-lg border border-[var(--border)] bg-white p-3 shadow-2xl">
          <input
            data-testid="model-picker-search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="mb-3 h-9 w-full rounded-lg border border-[var(--border)] px-3 text-sm outline-none focus:border-[var(--accent)]"
            placeholder={lang === 'zh' ? '搜索厂商或模型...' : 'Search provider or model...'}
          />
          <div className="max-h-80 overflow-y-auto pr-1">
            {filteredProviders.length === 0 ? (
              <div className="rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{lang === 'zh' ? '没有匹配的模型' : 'No matching models'}</div>
            ) : (
              filteredProviders.map((provider) => (
                <div key={provider.id} className="mb-3 last:mb-0">
                  <div className="mb-1 flex items-center justify-between px-1 text-xs font-semibold text-[var(--text-muted)]">
                    <span>{provider.label}</span>
                    <span className={provider.configured ? 'text-[var(--mint-strong)]' : 'text-[var(--amber)]'}>
                      {provider.configured ? (lang === 'zh' ? '已配置' : 'Configured') : (lang === 'zh' ? '未配置' : 'No key')}
                    </span>
                  </div>
                  <div className="space-y-1">
                    {provider.models.map((model) => {
                      const selected = provider.id === selection.provider && model.id === selection.model;
                      return (
                        <button
                          key={`${provider.id}-${model.id}`}
                          type="button"
                          data-testid={`model-option-${provider.id}-${toSafeAnchorPart(model.id)}`}
                          className={`flex w-full items-center justify-between gap-3 rounded-lg px-3 py-2 text-left text-sm transition ${
                            selected ? 'bg-[var(--bg-soft)] text-[var(--accent)]' : 'hover:bg-[var(--bg-soft)]'
                          }`}
                          onClick={() => {
                            onSelect({ provider: provider.id, model: model.id });
                            setOpen(false);
                            setQuery('');
                          }}
                        >
                          <span className="min-w-0">
                            <span className="block truncate font-medium">{model.name || model.id}</span>
                            {model.name ? <span className="block truncate text-xs text-[var(--text-muted)]">{model.id}</span> : null}
                          </span>
                          {selected ? <CheckCircle2 className="h-4 w-4 shrink-0" /> : null}
                        </button>
                      );
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function IconButton({ title, children, onClick, testId }: { title: string; children: React.ReactNode; onClick: () => void; testId?: string }) {
  return (
    <button data-testid={testId} className="grid h-8 w-8 place-items-center rounded-lg border border-[var(--border)] bg-white text-[var(--text-muted)] transition hover:border-[var(--border-strong)] hover:text-[var(--text-main)] sm:h-9 sm:w-9" title={title} onClick={onClick}>
      {children}
    </button>
  );
}

function EmptyChat({ lang, onPrompt }: { lang: Lang; onPrompt: (value: string) => void }) {
  return (
    <div className="grid min-h-[56vh] place-items-center">
      <div className="w-full max-w-2xl text-center">
        <div className="mx-auto mb-5 grid h-14 w-14 place-items-center rounded-lg bg-[var(--bg-panel)] text-[var(--accent)] shadow-sm ring-1 ring-[var(--border)]">
          <Bot className="h-7 w-7" />
        </div>
        <h1 className="text-2xl font-semibold text-[var(--text-main)]">{lang === 'zh' ? '今天要推进什么？' : 'What should we move forward?'}</h1>
        <div className="mt-5 grid gap-2 text-left sm:grid-cols-3">
          {quickPrompts[lang].map((prompt) => (
            <button key={prompt} className="min-w-0 overflow-hidden whitespace-normal break-words rounded-lg border border-[var(--border)] bg-white p-3 text-left text-sm leading-5 text-[var(--text-main)] shadow-sm transition hover:border-[var(--accent)] hover:shadow-md" onClick={() => onPrompt(prompt)}>
              {prompt}
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}

function UserMessage({ anchorId, content, userName }: { anchorId: string; content: string; userName?: string }) {
  return (
    <div id={anchorId} data-chat-anchor className="chat-anchor flex justify-end">
      <div className="flex max-w-[88%] items-start gap-3">
        <div className="min-w-0">
          {userName ? <div className="mb-1 text-right text-xs font-semibold text-[var(--text-muted)]">{userName}</div> : null}
          <div className="rounded-lg bg-[var(--accent)] px-4 py-3 text-sm leading-6 text-white shadow-sm">
            <TextBlock content={content} inverted />
          </div>
        </div>
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--bg-soft)] text-[var(--text-muted)]">
          <User className="h-4 w-4" />
        </div>
      </div>
    </div>
  );
}

function AgentMessage({
  anchorId,
  lang,
  traceAnchorId,
  children,
  thoughts,
  isStreaming,
}: {
  anchorId: string;
  lang: Lang;
  traceAnchorId: string;
  children: string;
  thoughts?: string[];
  isStreaming?: boolean;
}) {
  const hasContent = children.trim().length > 0;
  const currentThought = thoughts?.[thoughts.length - 1] || '';

  return (
    <div id={anchorId} data-chat-anchor className="chat-anchor flex items-start gap-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--bg-rail)] text-white">
        <Bot className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2 text-sm font-semibold">
          <span>YuanGe AI</span>
          <span className="rounded-md bg-[var(--mint-soft)] px-2 py-0.5 text-xs font-medium text-[var(--mint-strong)]">Agent</span>
        </div>
        {thoughts && thoughts.length > 0 && (
          <div id={traceAnchorId} data-chat-anchor className="chat-anchor mb-3 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] p-3 font-mono text-xs text-[var(--text-muted)]">
            <div className="mb-2 flex items-center gap-2 text-[var(--text-main)]">
              <Activity className="h-3.5 w-3.5 text-[var(--accent)]" />
              <span>{lang === 'zh' ? '轨迹' : 'Trace'}</span>
            </div>
            <div className="space-y-1.5">
              {thoughts.map((thought, idx) => (
                <div key={`${thought}-${idx}`} className="flex gap-2">
                  <span className="text-[var(--mint-strong)]">•</span>
                  <span className="break-words">{thought}</span>
                </div>
              ))}
              {isStreaming && <div className="text-[var(--accent)]">{lang === 'zh' ? '处理中...' : 'processing...'}</div>}
            </div>
          </div>
        )}
        <div className="rounded-lg border border-[var(--border)] bg-white px-4 py-3 text-sm leading-6 text-[var(--text-main)] shadow-sm">
          {hasContent ? <TextBlock content={children} /> : isStreaming ? <RunningProgress lang={lang} currentThought={currentThought} /> : <TextBlock content={children} />}
          {isStreaming && hasContent && <span className="ml-1 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-[var(--accent)]" />}
        </div>
      </div>
    </div>
  );
}

function RunningProgress({ lang, currentThought }: { lang: Lang; currentThought: string }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-[var(--accent)]">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="font-semibold">{lang === 'zh' ? '智能体正在处理' : 'Agent is working'}</span>
      </div>
      <div className="rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-xs leading-5 text-[var(--text-muted)]">
        {currentThought || (lang === 'zh' ? '正在等待后端进度...' : 'Waiting for backend progress...')}
      </div>
      <div className="space-y-2" aria-hidden="true">
        <div className="h-2 w-11/12 animate-pulse rounded-full bg-[var(--bg-soft)]" />
        <div className="h-2 w-8/12 animate-pulse rounded-full bg-[var(--bg-soft)]" />
        <div className="h-2 w-10/12 animate-pulse rounded-full bg-[var(--bg-soft)]" />
      </div>
    </div>
  );
}

function TextBlock({ content, inverted = false }: { content: string; inverted?: boolean }) {
  const blocks = useMemo(() => splitRichTextBlocks(content), [content]);
  if (!content) return null;

  return (
    <div className="rich-text min-w-0 break-words">
      {blocks.map((block, idx) =>
        block.type === 'code' ? (
          <CodeBlock key={`code-${idx}`} language={block.language} content={block.content} />
        ) : (
          <React.Fragment key={`text-${idx}`}>{renderMarkdownText(block.content, `text-${idx}`, inverted)}</React.Fragment>
        ),
      )}
    </div>
  );
}

function splitRichTextBlocks(content: string): RichTextBlock[] {
  const blocks: RichTextBlock[] = [];
  let cursor = 0;

  while (cursor < content.length) {
    const fenceStart = content.indexOf('```', cursor);
    if (fenceStart === -1) {
      blocks.push({ type: 'text', content: content.slice(cursor) });
      break;
    }

    if (fenceStart > cursor) {
      blocks.push({ type: 'text', content: content.slice(cursor, fenceStart) });
    }

    const languageEnd = content.indexOf('\n', fenceStart + 3);
    if (languageEnd === -1) {
      blocks.push({ type: 'code', language: '', content: content.slice(fenceStart + 3) });
      break;
    }

    const language = content.slice(fenceStart + 3, languageEnd).trim();
    const fenceEnd = content.indexOf('```', languageEnd + 1);
    if (fenceEnd === -1) {
      blocks.push({ type: 'code', language, content: content.slice(languageEnd + 1) });
      break;
    }

    blocks.push({ type: 'code', language, content: content.slice(languageEnd + 1, fenceEnd).replace(/\n$/, '') });
    cursor = fenceEnd + 3;
  }

  return blocks.filter((block) => block.content.length > 0);
}

function CodeBlock({ language, content }: { language?: string; content: string }) {
  const normalizedLanguage = (language || '').trim().toLowerCase();
  if (normalizedLanguage === 'mermaid') {
    const graph = parseCitationMermaid(content);
    if (graph.nodes.length || graph.edges.length) {
      return <CitationGraphPanel nodes={graph.nodes} edges={graph.edges} />;
    }
  }

  if (normalizedLanguage === 'json' || normalizedLanguage === 'javascript' || normalizedLanguage === 'js') {
    const graph = extractCitationGraphData(content);
    if (graph && (graph.nodes.length || graph.edges.length)) {
      return <CitationGraphPanel nodes={graph.nodes} edges={graph.edges} />;
    }
  }

  return (
    <div className="my-3 max-w-full overflow-hidden rounded-lg border border-slate-700 bg-[#111827] text-left">
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        showLineNumbers
        lineNumberStyle={{ color: '#4b5563', fontSize: '10px', minWidth: '2em' }}
        customStyle={{ background: 'transparent', margin: 0, padding: '14px', fontSize: '12px', lineHeight: 1.6 }}
        wrapLongLines
      >
        {content}
      </SyntaxHighlighter>
    </div>
  );
}

function CitationGraphPanel({ nodes, edges }: { nodes: CitationNode[]; edges: CitationEdge[] }) {
  const [view, setView] = useState<'graph' | 'list'>('graph');
  const citedByCount = useMemo(() => {
    const counts = new Map<string, number>();
    edges.forEach((edge) => counts.set(edge.target, (counts.get(edge.target) || 0) + 1));
    return counts;
  }, [edges]);

  return (
    <div className="my-3 overflow-hidden rounded-lg border border-[var(--border)] bg-white">
      <div className="flex flex-col gap-3 border-b border-[var(--border)] bg-[var(--bg-soft)] px-3 py-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-[var(--text-main)]">论文图谱</div>
          <div className="mt-0.5 text-xs text-[var(--text-muted)]">{nodes.length} 篇文献 · {edges.length} 条引用关系</div>
        </div>
        <div className="grid grid-cols-2 rounded-lg border border-[var(--border)] bg-white p-1 text-xs">
          <button
            type="button"
            className={`rounded-md px-3 py-1.5 transition ${view === 'graph' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)]'}`}
            onClick={() => setView('graph')}
          >
            引用关系
          </button>
          <button
            type="button"
            className={`rounded-md px-3 py-1.5 transition ${view === 'list' ? 'bg-[var(--accent)] text-white' : 'text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)]'}`}
            onClick={() => setView('list')}
          >
            文献列表
          </button>
        </div>
      </div>
      {view === 'graph' ? (
        <CitationGraph nodes={nodes} edges={edges} />
      ) : (
        <PaperReferenceList nodes={nodes} citedByCount={citedByCount} />
      )}
    </div>
  );
}

function PaperReferenceList({ nodes, citedByCount }: { nodes: CitationNode[]; citedByCount: Map<string, number> }) {
  const sortedNodes = [...nodes].sort((a, b) => {
    const aYear = Number(a.year || 0);
    const bYear = Number(b.year || 0);
    return bYear - aYear || Number(b.citations || 0) - Number(a.citations || 0);
  });

  if (!sortedNodes.length) {
    return <div className="p-4 text-sm text-[var(--text-muted)]">暂无文献节点。</div>;
  }

  return (
    <div className="max-h-[460px] overflow-y-auto p-3">
      <div className="space-y-2">
        {sortedNodes.map((node, index) => (
          <div key={`${node.id}-${index}`} className="rounded-lg border border-[var(--border)] bg-white p-3 shadow-sm">
            <div className="flex items-start gap-3">
              <div className="grid h-7 w-7 shrink-0 place-items-center rounded-md bg-[var(--bg-soft)] text-xs font-semibold text-[var(--accent)]">
                {index + 1}
              </div>
              <div className="min-w-0 flex-1">
                <div className="break-words text-sm font-semibold leading-5 text-[var(--text-main)]">{node.title || node.id}</div>
                <div className="mt-1 flex flex-wrap gap-2 text-xs text-[var(--text-muted)]">
                  {node.year ? <span>{node.year}</span> : null}
                  {node.venue ? <span>{node.venue}</span> : null}
                  {typeof node.citations === 'number' && node.citations > 0 ? <span>{node.citations} citations</span> : null}
                  {citedByCount.get(node.id) ? <span>图中被引用 {citedByCount.get(node.id)} 次</span> : null}
                  {node.external ? <span className="rounded-md bg-[var(--bg-soft)] px-1.5 py-0.5">扩展引用</span> : null}
                </div>
                <div className="mt-1 font-mono text-[10px] text-[var(--text-muted)]">{node.id}</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function extractCitationGraphData(content: string): { nodes: CitationNode[]; edges: CitationEdge[] } | null {
  try {
    const parsed = JSON.parse(content);
    const graph = findCitationGraphPayload(parsed);
    if (!graph) return null;
    const nodes = Array.isArray(graph.nodes) ? graph.nodes : [];
    const edges = Array.isArray(graph.edges) ? graph.edges : [];
    if (!nodes.length && !edges.length) return null;
    return {
      nodes: nodes.map((node: any, index: number) => ({
        id: String(node.id || node.title || `paper-${index}`),
        title: String(node.title || node.label || node.id || `Paper ${index + 1}`),
        year: node.year || node.publication_year,
        venue: node.venue || node.source || '',
        citations: Number(node.citations || node.cited_by_count || 0),
        external: Boolean(node.external),
      })),
      edges: edges.map((edge: any) => ({
        source: String(edge.source || edge.from || ''),
        target: String(edge.target || edge.to || ''),
        type: edge.type || edge.label || 'cites',
      })).filter((edge: CitationEdge) => edge.source && edge.target),
    };
  } catch {
    return null;
  }
}

function findCitationGraphPayload(value: any): any | null {
  if (!value || typeof value !== 'object') return null;
  if (Array.isArray(value.nodes) && Array.isArray(value.edges)) return value;
  if (value.citation_graph) return findCitationGraphPayload(value.citation_graph);
  if (value.graph) return findCitationGraphPayload(value.graph);
  if (value.result) return findCitationGraphPayload(value.result);
  if (typeof value.output === 'string') {
    try {
      return findCitationGraphPayload(JSON.parse(value.output));
    } catch {
      return null;
    }
  }
  return null;
}

function renderMarkdownText(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const nodes: React.ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      nodes.push(
        React.createElement(
          `h${level + 1}`,
          {
            key: `${keyPrefix}-heading-${index}`,
            className: `${level <= 2 ? 'mt-4 text-base' : 'mt-3 text-sm'} mb-2 font-semibold leading-6 first:mt-0`,
          },
          renderInlineWithBreaks(heading[2], `${keyPrefix}-heading-${index}`, inverted),
        ),
      );
      index += 1;
      continue;
    }

    if (/^[-*_]{3,}$/.test(trimmed)) {
      nodes.push(<hr key={`${keyPrefix}-hr-${index}`} className="my-3 border-[var(--border)]" />);
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        tableLines.push(lines[index]);
        index += 1;
      }
      nodes.push(renderTable(tableLines, `${keyPrefix}-table-${index}`, inverted));
      continue;
    }

    const listMatch = getListMatch(line);
    if (listMatch) {
      const ordered = /^\d/.test(listMatch.marker);
      const items: string[] = [];
      while (index < lines.length) {
        const currentMatch = getListMatch(lines[index]);
        if (!currentMatch || /^\d/.test(currentMatch.marker) !== ordered) break;
        items.push(currentMatch.content);
        index += 1;
      }
      const Tag = ordered ? 'ol' : 'ul';
      nodes.push(
        React.createElement(
          Tag,
          {
            key: `${keyPrefix}-list-${index}`,
            className: `${ordered ? 'list-decimal' : 'list-disc'} my-2 space-y-1 pl-5`,
          },
          items.map((item, itemIndex) => (
            <li key={`${keyPrefix}-list-${index}-${itemIndex}`}>{renderInlineWithBreaks(item, `${keyPrefix}-li-${index}-${itemIndex}`, inverted)}</li>
          )),
        ),
      );
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ''));
        index += 1;
      }
      nodes.push(
        <blockquote key={`${keyPrefix}-quote-${index}`} className="my-2 border-l-2 border-[var(--border-strong)] pl-3 text-[var(--text-muted)]">
          {renderInlineWithBreaks(quoteLines.join('\n'), `${keyPrefix}-quote-${index}`, inverted)}
        </blockquote>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines, index)) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    nodes.push(
      <p key={`${keyPrefix}-paragraph-${index}`} className="my-2 first:mt-0 last:mb-0">
        {renderInlineWithBreaks(paragraphLines.join('\n'), `${keyPrefix}-paragraph-${index}`, inverted)}
      </p>,
    );
  }

  return nodes;
}

function isMarkdownBlockStart(lines: string[], index: number) {
  const line = lines[index] || '';
  return /^(#{1,6})\s+/.test(line) || /^[-*_]{3,}$/.test(line.trim()) || Boolean(getListMatch(line)) || /^>\s?/.test(line) || isTableStart(lines, index);
}

function getListMatch(line: string) {
  const match = line.match(/^\s*(?<marker>[-*]|\d+[.)])\s+(?<content>.+)$/);
  if (!match?.groups) return null;
  return { marker: match.groups.marker, content: match.groups.content };
}

function isTableStart(lines: string[], index: number) {
  return Boolean(lines[index]?.includes('|') && lines[index + 1] && isTableSeparator(lines[index + 1]));
}

function isTableSeparator(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}

function splitTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}

function renderTable(tableLines: string[], key: string, inverted: boolean) {
  const headers = splitTableRow(tableLines[0]);
  const rows = tableLines.slice(2).map(splitTableRow);

  return (
    <div key={key} className="my-3 max-w-full overflow-x-auto rounded-lg border border-[var(--border)]">
      <table className="min-w-full border-collapse text-left text-sm">
        <thead className="bg-[var(--bg-soft)]">
          <tr>
            {headers.map((header, idx) => (
              <th key={`${key}-head-${idx}`} className="border-b border-[var(--border)] px-3 py-2 font-semibold">
                {renderInlineWithBreaks(header, `${key}-head-${idx}`, inverted)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${key}-row-${rowIndex}`} className="border-t border-[var(--border)] first:border-t-0">
              {headers.map((_, cellIndex) => (
                <td key={`${key}-cell-${rowIndex}-${cellIndex}`} className="px-3 py-2 align-top">
                  {renderInlineWithBreaks(row[cellIndex] || '', `${key}-cell-${rowIndex}-${cellIndex}`, inverted)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function renderInlineWithBreaks(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  return text.split('\n').flatMap((line, index, allLines) => {
    const rendered = renderInlineContent(line, `${keyPrefix}-line-${index}`, inverted);
    if (index === allLines.length - 1) return rendered;
    return [...rendered, <br key={`${keyPrefix}-br-${index}`} />];
  });
}

function renderInlineContent(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  const parts = text.split(/(`[^`\n]*`)/g);
  const nodes: React.ReactNode[] = [];
  parts.forEach((part, idx) => {
    if (!part) return;
    if (/^`[^`\n]*`$/.test(part)) {
      nodes.push(
        <code key={`${keyPrefix}-code-${idx}`} className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.92em]">
          {part.slice(1, -1)}
        </code>,
      );
      return;
    }
    nodes.push(...renderLinkedText(part, `${keyPrefix}-text-${idx}`, inverted));
  });
  return nodes;
}

function renderLinkedText(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  const tokenPattern = /\[[^\]\n]+\]\((?:https?:\/\/|www\.|#|\/|mailto:)[^\s)]*\)|(?:https?:\/\/|www\.)[^\s<]+|(?:doi:\s*)?10\.\d{4,9}\/[-._;()/:A-Z0-9]+/gi;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(text))) {
    if (match.index > lastIndex) {
      nodes.push(...renderDecoratedText(text.slice(lastIndex, match.index), `${keyPrefix}-plain-${lastIndex}`));
    }

    const token = match[0];
    const markdownLink = token.match(/^\[([^\]\n]+)\]\((.+)\)$/);
    if (markdownLink) {
      nodes.push(
        <RichLink key={`${keyPrefix}-link-${match.index}`} href={markdownLink[2]} inverted={inverted}>
          {markdownLink[1]}
        </RichLink>,
      );
    } else {
      const { value, trailing } = splitTrailingPunctuation(token);
      nodes.push(
        <RichLink key={`${keyPrefix}-link-${match.index}`} href={value} inverted={inverted}>
          {value}
        </RichLink>,
      );
      if (trailing) nodes.push(trailing);
    }

    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    nodes.push(...renderDecoratedText(text.slice(lastIndex), `${keyPrefix}-plain-${lastIndex}`));
  }

  return nodes;
}

function renderDecoratedText(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const boldPattern = /(\*\*[^*\n]+\*\*|__[^_\n]+__)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = boldPattern.exec(text))) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    nodes.push(
      <strong key={`${keyPrefix}-bold-${match.index}`} className="font-semibold">
        {match[0].slice(2, -2)}
      </strong>,
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}

function splitTrailingPunctuation(token: string) {
  let value = token;
  let trailing = '';
  while (/[.,;:!?，。；：！？]$/.test(value)) {
    trailing = value.slice(-1) + trailing;
    value = value.slice(0, -1);
  }
  return { value, trailing };
}

function normalizeHref(href: string) {
  const trimmed = href.trim();
  if (/^doi:\s*/i.test(trimmed)) return `https://doi.org/${trimmed.replace(/^doi:\s*/i, '')}`;
  if (/^10\.\d{4,9}\//i.test(trimmed)) return `https://doi.org/${trimmed}`;
  if (/^www\./i.test(trimmed)) return `https://${trimmed}`;
  return trimmed;
}

function RichLink({ href, inverted, children }: { href: string; inverted: boolean; children: React.ReactNode }) {
  const normalizedHref = normalizeHref(href);
  const isHashLink = normalizedHref.startsWith('#');
  const isExternal = /^(https?:\/\/|mailto:)/i.test(normalizedHref);
  const shouldValidate = /^https?:\/\//i.test(normalizedHref);
  const [validation, setValidation] = useState<{ status: LinkValidationStatus; code?: number }>(() => linkValidationCache.get(normalizedHref) || { status: 'idle' });
  const className = inverted
    ? 'font-medium text-white underline decoration-white/60 underline-offset-2 hover:decoration-white'
    : 'font-medium text-[var(--accent-strong)] underline decoration-[var(--accent)]/35 underline-offset-2 hover:decoration-[var(--accent)]';

  const validateLink = useCallback(() => {
    if (!shouldValidate || validation.status === 'checking' || validation.status === 'ok' || validation.status === 'bad') return;
    const cached = linkValidationCache.get(normalizedHref);
    if (cached) {
      setValidation(cached);
      return;
    }
    const controller = new AbortController();
    setValidation({ status: 'checking' });
    fetch(`${API_BASE}/validate_link?url=${encodeURIComponent(normalizedHref)}`, { signal: controller.signal })
      .then((response) => response.json())
      .then((data) => {
        const next =
          data?.ok
            ? { status: 'ok' as const, code: data.status }
            : typeof data?.status === 'number' && data.status > 0
              ? { status: 'bad' as const, code: data.status }
              : { status: 'unknown' as const };
        linkValidationCache.set(normalizedHref, next);
        setValidation(next);
      })
      .catch(() => setValidation({ status: 'unknown' }));
  }, [normalizedHref, shouldValidate, validation.status]);

  return (
    <>
      <a
        href={normalizedHref}
        className={className}
        target={isExternal ? '_blank' : undefined}
        rel={isExternal ? 'noreferrer' : undefined}
        onMouseEnter={validateLink}
        onFocus={validateLink}
        onClick={(event) => {
          if (!isHashLink) return;
          event.preventDefault();
          const anchorId = normalizedHref.slice(1);
          scrollToAnchor(anchorId);
        }}
      >
        {children}
      </a>
      {shouldValidate && <LinkStatusBadge status={validation.status} code={validation.code} />}
    </>
  );
}

function LinkStatusBadge({ status, code }: { status: LinkValidationStatus; code?: number }) {
  if (status === 'idle') return null;
  if (status === 'checking') {
    return <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-[var(--text-muted)] align-middle opacity-45" title="Checking link" />;
  }
  if (status === 'ok') {
    return <CheckCircle2 className="ml-1 inline-block h-3.5 w-3.5 align-[-2px] text-[var(--mint-strong)]" aria-label="Link available" />;
  }
  if (status === 'bad') {
    return (
      <span className="ml-1 inline-flex items-center gap-0.5 align-middle text-[var(--danger)]" title={`Link may be unavailable${code ? ` (${code})` : ''}`}>
        <AlertTriangle className="h-3.5 w-3.5" />
        {code ? <span className="font-mono text-[10px]">{code}</span> : null}
      </span>
    );
  }
  return <AlertTriangle className="ml-1 inline-block h-3.5 w-3.5 align-[-2px] text-[var(--amber)]" aria-label="Link not checked" />;
}

function clampPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, value));
}

function formatUptime(seconds?: number) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds) || seconds < 0) return '--';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${Math.max(0, minutes)}m`;
}

function formatUpdatedAt(timestamp: number | undefined, lang: Lang) {
  if (!timestamp) return lang === 'zh' ? '等待数据' : 'Waiting';
  const value = new Date(timestamp * 1000);
  return value.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function statusText(status: RuntimeServiceStatus['status'], lang: Lang) {
  if (status === 'online') return lang === 'zh' ? '在线' : 'Online';
  if (status === 'offline') return lang === 'zh' ? '离线' : 'Offline';
  return lang === 'zh' ? '未知' : 'Unknown';
}

function RuntimePanel({
  t,
  lang,
  navItems,
  activeAnchorId,
  onNavigate,
  onClose,
}: {
  t: ConfigText;
  lang: Lang;
  navItems: ConversationNavItem[];
  activeAnchorId: string;
  onNavigate: (anchorId: string) => void;
  onClose: () => void;
}) {
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [runtimeError, setRuntimeError] = useState('');
  const [runtimeLoading, setRuntimeLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let activeController: AbortController | null = null;

    const fetchRuntimeStatus = async () => {
      if (inFlight) return;
      inFlight = true;
      const started = performance.now();
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 2400);
      try {
        const response = await fetch(`${API_BASE}/runtime_status`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as RuntimeStatus;
        if (cancelled) return;
        setRuntimeStatus({
          ...data,
          latency_ms: typeof data.latency_ms === 'number' ? data.latency_ms : Math.round(performance.now() - started),
        });
        setRuntimeError('');
      } catch (error) {
        if (cancelled) return;
        setRuntimeStatus(null);
        const isAbort = error instanceof DOMException && error.name === 'AbortError';
        setRuntimeError(isAbort ? (lang === 'zh' ? '状态接口超时' : 'Status timeout') : (lang === 'zh' ? '后端未连接' : 'Backend offline'));
      } finally {
        window.clearTimeout(timeout);
        if (!cancelled) setRuntimeLoading(false);
        inFlight = false;
        activeController = null;
      }
    };

    fetchRuntimeStatus();
    const interval = window.setInterval(fetchRuntimeStatus, 3000);
    return () => {
      cancelled = true;
      activeController?.abort();
      window.clearInterval(interval);
    };
  }, [lang]);

  const browserCores = typeof navigator !== 'undefined' ? navigator.hardwareConcurrency || 0 : 0;
  const cores = runtimeStatus?.system?.logical_cores || browserCores;
  const metrics = useMemo(() => {
    return {
      cpu: clampPercent(runtimeStatus?.system?.cpu_percent),
      ram: clampPercent(runtimeStatus?.system?.memory_percent),
      disk: clampPercent(runtimeStatus?.system?.disk_percent),
      latency: typeof runtimeStatus?.latency_ms === 'number' ? runtimeStatus.latency_ms : 0,
      skills: runtimeStatus?.skills?.total ?? 0,
    };
  }, [runtimeStatus]);
  const isOnline = runtimeStatus?.backend?.status === 'online' || runtimeStatus?.ok === true;
  const isDegraded = isOnline && runtimeStatus?.backend?.agent_status === 'degraded';
  const backendLabel = isDegraded ? (lang === 'zh' ? '降级' : 'Degraded') : isOnline ? (lang === 'zh' ? '在线' : 'Online') : runtimeLoading ? (lang === 'zh' ? '检测中' : 'Checking') : (lang === 'zh' ? '离线' : 'Offline');
  const updatedLabel = runtimeStatus ? `${lang === 'zh' ? '更新' : 'Updated'} ${formatUpdatedAt(runtimeStatus.timestamp, lang)}` : runtimeError || (lang === 'zh' ? '等待状态' : 'Waiting for status');
  const services = runtimeStatus?.services || [];

  const localDetails = useMemo(() => {
    const details: string[] = [];
    if (runtimeStatus?.backend?.agent_status === 'degraded') details.push(lang === 'zh' ? '智能体降级' : 'Agent degraded');
    if (runtimeStatus?.backend?.pid) details.push(`PID ${runtimeStatus.backend.pid}`);
    if (runtimeStatus?.process?.memory_mb) details.push(`${runtimeStatus.process.memory_mb.toFixed(1)} MB`);
    if (runtimeStatus?.process?.threads) details.push(lang === 'zh' ? `${runtimeStatus.process.threads} 线程` : `${runtimeStatus.process.threads} threads`);
    return details.join(' · ');
  }, [lang, runtimeStatus]);

  return (
    <div className="flex h-full w-[360px] flex-col border-l border-[var(--border)] bg-[var(--bg-panel)] shadow-2xl">
      <div className="flex h-16 items-center justify-between border-b border-[var(--border)] px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--bg-soft)] text-[var(--accent)]">
            <Server className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{t.monitorTitle}</div>
            <div className="truncate text-xs text-[var(--text-muted)]">
              {cores ? (lang === 'zh' ? `${cores} 逻辑核心` : `${cores} logical cores`) : updatedLabel}
            </div>
          </div>
        </div>
        <button className="rounded-lg p-2 text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)] xl:hidden" onClick={onClose} aria-label="Close monitor">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <ConversationNavigator lang={lang} items={navItems} activeAnchorId={activeAnchorId} onNavigate={onNavigate} />

        <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-[var(--mint-strong)]" />
            <div className="text-sm font-semibold">{t.monitorAirGap}</div>
          </div>
          <p className="text-sm leading-6 text-[var(--text-muted)]">{t.monitorAirDesc}</p>
          <div className="mt-4 flex items-center justify-between rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm">
            <span className="text-[var(--text-muted)]">{t.monitorLockLabel}</span>
            <span className={`flex items-center gap-2 font-semibold ${isDegraded ? 'text-[var(--amber)]' : isOnline ? 'text-[var(--mint-strong)]' : runtimeLoading ? 'text-[var(--amber)]' : 'text-[var(--danger)]'}`}>
              <span className={`h-2 w-2 rounded-full ${isDegraded ? 'bg-[var(--amber)]' : isOnline ? 'bg-[var(--ok)]' : runtimeLoading ? 'bg-[var(--amber)]' : 'bg-[var(--danger)]'}`} />
              {backendLabel}
            </span>
          </div>
          <div className="mt-2 text-xs text-[var(--text-muted)]">{updatedLabel}</div>
        </div>

        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-4 text-sm font-semibold">{t.monitorHardware}</div>
          <MetricBar icon={<Activity className="h-4 w-4" />} label={t.monitorCpu} value={metrics.cpu} accent="var(--accent)" detail={runtimeStatus ? `${metrics.cpu.toFixed(0)}%` : '--'} />
          <MetricBar icon={<Database className="h-4 w-4" />} label={t.monitorMem} value={metrics.ram} accent="var(--mint-strong)" detail={runtimeStatus ? `${metrics.ram.toFixed(0)}%` : '--'} />
          <MetricBar icon={<Globe2 className="h-4 w-4" />} label={t.monitorNet} value={metrics.disk} accent="var(--amber)" detail={runtimeStatus ? `${metrics.disk.toFixed(0)}%` : '--'} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <SmallMetric label={t.monitorCpuLoad} value={runtimeStatus ? String(metrics.skills) : '--'} icon={<Code2 className="h-4 w-4" />} />
          <SmallMetric label={t.monitorBattery} value={runtimeStatus ? `${metrics.latency}ms` : '--'} icon={<Activity className="h-4 w-4" />} />
        </div>

        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between text-sm font-semibold">
            <span>{lang === 'zh' ? '服务探测' : 'Service Checks'}</span>
            <span className="font-mono text-xs text-[var(--text-muted)]">{services.length}</span>
          </div>
          {services.length === 0 ? (
            <div className="rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{runtimeError || (lang === 'zh' ? '暂无状态数据' : 'No status data')}</div>
          ) : (
            <div className="space-y-2">
              {services.map((service) => (
                <div key={service.name} className="flex items-center justify-between gap-3 rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${service.status === 'online' ? 'bg-[var(--ok)]' : service.status === 'offline' ? 'bg-[var(--danger)]' : 'bg-[var(--amber)]'}`} />
                    <span className="truncate font-medium text-[var(--text-main)]">{service.label || service.name}</span>
                  </span>
                  <span className="shrink-0 font-mono text-xs text-[var(--text-muted)]">
                    {statusText(service.status, lang)}
                    {service.port ? `:${service.port}` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] p-4 text-sm text-[var(--text-muted)]">
          <div className="mb-1 font-semibold text-[var(--text-main)]">{t.monitorLocal}</div>
          <div>{localDetails || updatedLabel}</div>
          {runtimeStatus?.backend?.startup_error && (
            <div className="mt-2 overflow-hidden text-ellipsis whitespace-nowrap text-[var(--amber)]" title={runtimeStatus.backend.startup_error}>
              {runtimeStatus.backend.startup_error}
            </div>
          )}
          {runtimeStatus?.backend?.uptime_sec !== undefined && (
            <div className="mt-2">{lang === 'zh' ? '运行时长' : 'Uptime'} {formatUptime(runtimeStatus.backend.uptime_sec)}</div>
          )}
        </div>
      </div>
    </div>
  );
}

function ConversationNavigator({
  lang,
  items,
  activeAnchorId,
  onNavigate,
}: {
  lang: Lang;
  items: ConversationNavItem[];
  activeAnchorId: string;
  onNavigate: (anchorId: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(true);

  return (
    <div className="mb-4 rounded-lg border border-[var(--border)] bg-white p-3 shadow-sm">
      <button
        type="button"
        data-testid="conversation-nav-toggle"
        className="flex w-full items-center justify-between gap-3 rounded-lg px-1 py-1 text-left text-sm font-semibold text-[var(--text-main)] hover:bg-[var(--bg-soft)]"
        aria-expanded={isOpen}
        onClick={() => setIsOpen((current) => !current)}
      >
        <span className="flex min-w-0 items-center gap-2">
          <FileText className="h-4 w-4 shrink-0 text-[var(--accent)]" />
          <span className="truncate">{lang === 'zh' ? '对话导航' : 'Conversation'}</span>
          <span className="rounded-md bg-[var(--bg-soft)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">{items.length}</span>
        </span>
        <ChevronDown className={`h-4 w-4 shrink-0 text-[var(--text-muted)] transition-transform ${isOpen ? 'rotate-180' : ''}`} />
      </button>
      {isOpen && (
        <div className="mt-3">
          {items.length === 0 ? (
            <div className="rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{lang === 'zh' ? '暂无对话' : 'No messages yet'}</div>
          ) : (
            <div className="space-y-2">
              {items.map((item) => (
                <ConversationNavButton key={item.anchorId} item={item} active={activeAnchorId === item.anchorId} onNavigate={onNavigate} />
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function ConversationNavButton({
  item,
  active,
  onNavigate,
}: {
  item: ConversationNavItem;
  active: boolean;
  onNavigate: (anchorId: string) => void;
}) {
  const Icon = item.kind === 'user' ? User : item.kind === 'trace' ? Activity : Bot;
  return (
    <button
      type="button"
      data-testid="conversation-nav-item"
      data-anchor-id={item.anchorId}
      className={`w-full rounded-lg border px-3 py-2 text-left transition ${
        active
          ? 'border-[var(--accent)] bg-[var(--bg-soft)] shadow-sm'
          : 'border-[var(--border)] bg-white hover:border-[var(--border-strong)] hover:bg-[var(--bg-soft)]'
      }`}
      onClick={() => onNavigate(item.anchorId)}
    >
      <div className="mb-1 flex min-w-0 items-center gap-2">
        <Icon className={`h-3.5 w-3.5 shrink-0 ${active ? 'text-[var(--accent)]' : 'text-[var(--text-muted)]'}`} />
        <span className="truncate text-xs font-semibold text-[var(--text-main)]">{item.label}</span>
      </div>
      <div
        className="overflow-hidden text-xs leading-5 text-[var(--text-muted)]"
        style={{ display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical' }}
      >
        {item.summary}
      </div>
    </button>
  );
}

function MetricBar({ icon, label, value, accent, detail }: { icon: React.ReactNode; label: string; value: number; accent: string; detail?: string }) {
  return (
    <div className="mb-4 last:mb-0">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="flex min-w-0 items-center gap-2 text-[var(--text-muted)]">
          {icon}
          <span className="truncate">{label}</span>
        </span>
        <span className="font-mono text-[var(--text-main)]">{detail ?? `${value.toFixed(0)}%`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-soft)]">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${value}%`, background: accent }} />
      </div>
    </div>
  );
}

function SmallMetric({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between text-[var(--text-muted)]">
        <span className="truncate text-sm">{label}</span>
        {icon}
      </div>
      <div className="font-mono text-2xl font-semibold text-[var(--text-main)]">{value}</div>
    </div>
  );
}

function EvolutionManagerModal({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const [proposals, setProposals] = useState<EvolutionProposal[]>([]);
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [title, setTitle] = useState('');
  const [summary, setSummary] = useState('');
  const [targetPath, setTargetPath] = useState('docs/evolution-notes.md');
  const [content, setContent] = useState('');

  const loadProposals = useCallback(async () => {
    setLoading(true);
    setStatus('');
    try {
      const res = await fetch(`${API_BASE}/evolution/proposals?include_content=true`, { cache: 'no-store' });
      const data: EvolutionProposalsResponse = await res.json();
      if (!res.ok || data.status !== 'success') throw new Error(data.message || `HTTP ${res.status}`);
      setProposals(data.proposals || []);
    } catch {
      setProposals([]);
      setStatus(lang === 'zh' ? '后端未启动或提案库不可用' : 'Backend offline or proposal store unavailable');
    } finally {
      setLoading(false);
    }
  }, [lang]);

  useEffect(() => {
    loadProposals();
  }, [loadProposals]);

  const createProposal = async () => {
    const cleanTitle = title.trim();
    const cleanSummary = summary.trim();
    const cleanPath = targetPath.trim();
    if (!cleanTitle || !cleanSummary || !cleanPath) {
      setStatus(lang === 'zh' ? '标题、摘要和路径必填' : 'Title, summary, and path are required');
      return;
    }
    setStatus(lang === 'zh' ? '正在创建提案...' : 'Creating proposal...');
    try {
      const res = await fetch(`${API_BASE}/evolution/proposals`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          title: cleanTitle,
          summary: cleanSummary,
          kind: 'project',
          author: 'frontend',
          files: [{ path: cleanPath, action: 'write', content, summary: cleanSummary }],
          notes: [lang === 'zh' ? '由前端审查面板创建' : 'Created from the frontend review panel'],
        }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'success') throw new Error(data.message || 'create failed');
      setTitle('');
      setSummary('');
      setContent('');
      setStatus(lang === 'zh' ? '提案已创建，等待应用' : 'Proposal created and waiting for review');
      await loadProposals();
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      setStatus(message || (lang === 'zh' ? '创建失败' : 'Create failed'));
    }
  };

  const runProposalAction = async (proposal: EvolutionProposal, action: 'apply' | 'reject' | 'rollback') => {
    const labels = {
      apply: lang === 'zh' ? '应用' : 'apply',
      reject: lang === 'zh' ? '拒绝' : 'reject',
      rollback: lang === 'zh' ? '回滚' : 'rollback',
    };
    const ok = window.confirm(lang === 'zh' ? `确认${labels[action]}这个提案？` : `Confirm ${labels[action]} this proposal?`);
    if (!ok) return;
    setStatus(lang === 'zh' ? `正在${labels[action]}...` : `Running ${labels[action]}...`);
    try {
      const res = await fetch(`${API_BASE}/evolution/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: proposal.id, reviewer: 'frontend' }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'success') throw new Error(data.message || `${action} failed`);
      setStatus(lang === 'zh' ? '操作完成' : 'Action complete');
      await loadProposals();
    } catch (error) {
      const message = error instanceof Error ? error.message : '';
      setStatus(message || (lang === 'zh' ? '操作失败' : 'Action failed'));
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-5xl flex-col rounded-lg border border-[var(--border)] bg-white shadow-2xl">
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-semibold text-[var(--text-main)]">
              <ShieldCheck className="h-4 w-4 text-[var(--accent)]" />
              <span>{lang === 'zh' ? '受控自进化审查' : 'Controlled Evolution Review'}</span>
            </div>
            <div className="mt-1 text-xs text-[var(--text-muted)]">
              {lang === 'zh' ? '先生成提案，应用前自动快照，应用后可回滚。' : 'Create proposals first; applying snapshots files so changes can be rolled back.'}
            </div>
          </div>
          <button className="grid h-8 w-8 place-items-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)]" onClick={onClose} aria-label="Close evolution manager">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {status ? <div className="mb-3 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{status}</div> : null}
          <div className="grid gap-4 lg:grid-cols-[360px_minmax(0,1fr)]">
            <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
              <div className="mb-3 text-sm font-semibold text-[var(--text-main)]">{lang === 'zh' ? '新建提案' : 'New proposal'}</div>
              <div className="space-y-3">
                <input
                  value={title}
                  onChange={(event) => setTitle(event.target.value)}
                  className="h-10 w-full rounded-lg border border-[var(--border)] px-3 text-sm outline-none focus:border-[var(--accent)]"
                  placeholder={lang === 'zh' ? '标题' : 'Title'}
                />
                <input
                  value={targetPath}
                  onChange={(event) => setTargetPath(event.target.value)}
                  className="h-10 w-full rounded-lg border border-[var(--border)] px-3 font-mono text-sm outline-none focus:border-[var(--accent)]"
                  placeholder="docs/evolution-notes.md"
                />
                <textarea
                  value={summary}
                  onChange={(event) => setSummary(event.target.value)}
                  className="min-h-20 w-full resize-y rounded-lg border border-[var(--border)] p-3 text-sm leading-6 outline-none focus:border-[var(--accent)]"
                  placeholder={lang === 'zh' ? '摘要：为什么要改' : 'Summary: why this change matters'}
                />
                <textarea
                  value={content}
                  onChange={(event) => setContent(event.target.value)}
                  className="min-h-40 w-full resize-y rounded-lg border border-[var(--border)] p-3 font-mono text-xs leading-5 outline-none focus:border-[var(--accent)]"
                  placeholder={lang === 'zh' ? '目标文件的新内容' : 'Full replacement content for the target file'}
                />
                <button className="inline-flex h-10 w-full items-center justify-center gap-2 rounded-lg bg-[var(--accent)] px-3 text-sm text-white hover:bg-[var(--accent-strong)]" onClick={createProposal} disabled={loading}>
                  <Save className="h-4 w-4" />
                  {lang === 'zh' ? '创建审查提案' : 'Create proposal'}
                </button>
              </div>
            </div>

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
                <div className="space-y-3">
                  {proposals.map((proposal) => (
                    <EvolutionProposalCard key={proposal.id} lang={lang} proposal={proposal} onAction={runProposalAction} />
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function EvolutionProposalCard({
  lang,
  proposal,
  onAction,
}: {
  lang: Lang;
  proposal: EvolutionProposal;
  onAction: (proposal: EvolutionProposal, action: 'apply' | 'reject' | 'rollback') => void;
}) {
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

function guessCodeLanguage(pathValue: string) {
  if (/\.tsx?$/i.test(pathValue)) return 'tsx';
  if (/\.jsx?$/i.test(pathValue)) return 'javascript';
  if (/\.py$/i.test(pathValue)) return 'python';
  if (/\.ya?ml$/i.test(pathValue)) return 'yaml';
  if (/\.json$/i.test(pathValue)) return 'json';
  if (/\.md$/i.test(pathValue)) return 'markdown';
  return 'text';
}

function MemoryManagerModal({ lang, onClose }: { lang: Lang; onClose: () => void }) {
  const [records, setRecords] = useState<MemoryRecord[]>([]);
  const [total, setTotal] = useState(0);
  const [query, setQuery] = useState('');
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState('');
  const [editingId, setEditingId] = useState('');
  const [draftText, setDraftText] = useState('');

  const loadRecords = useCallback(async () => {
    setLoading(true);
    setStatus('');
    try {
      const params = new URLSearchParams();
      if (query.trim()) params.set('query', query.trim());
      params.set('limit', '100');
      const res = await fetch(`${API_BASE}/memory/records?${params.toString()}`, { cache: 'no-store' });
      const data: MemoryRecordsResponse = await res.json();
      if (!res.ok || data.status === 'degraded') throw new Error(data.message || `HTTP ${res.status}`);
      setRecords(data.records || []);
      setTotal(data.total || 0);
    } catch (error) {
      setRecords([]);
      setTotal(0);
      setStatus(lang === 'zh' ? '后端未启动或记忆库不可用' : 'Backend offline or memory database unavailable');
    } finally {
      setLoading(false);
    }
  }, [lang, query]);

  useEffect(() => {
    loadRecords();
  }, [loadRecords]);

  const startEdit = (record: MemoryRecord) => {
    setEditingId(record.id);
    setDraftText(record.text || '');
  };

  const saveEdit = async (record: MemoryRecord) => {
    const text = draftText.trim();
    if (!text) return;
    setStatus(lang === 'zh' ? '正在保存...' : 'Saving...');
    try {
      const res = await fetch(`${API_BASE}/memory/update`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: record.id, text, owner_id: record.owner_id, scope: record.scope, session_id: record.session_id, metadata: record.metadata || {} }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'success') throw new Error(data.message || 'save failed');
      setEditingId('');
      setDraftText('');
      setStatus(lang === 'zh' ? '已保存' : 'Saved');
      await loadRecords();
    } catch {
      setStatus(lang === 'zh' ? '保存失败' : 'Save failed');
    }
  };

  const deleteRecord = async (record: MemoryRecord) => {
    const ok = window.confirm(lang === 'zh' ? '确认删除这条长期记忆？' : 'Delete this memory record?');
    if (!ok) return;
    setStatus(lang === 'zh' ? '正在删除...' : 'Deleting...');
    try {
      const res = await fetch(`${API_BASE}/memory/delete`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: record.id }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== 'success') throw new Error(data.message || 'delete failed');
      setStatus(lang === 'zh' ? '已删除' : 'Deleted');
      await loadRecords();
    } catch {
      setStatus(lang === 'zh' ? '删除失败' : 'Delete failed');
    }
  };

  const clearMemory = async () => {
    const ok = window.confirm(lang === 'zh' ? '确认清空所有长期记忆？前端对话不会被删除。' : 'Clear all long-term memory? Frontend chats will remain.');
    if (!ok) return;
    setStatus(lang === 'zh' ? '正在清空长期记忆...' : 'Clearing long-term memory...');
    try {
      const res = await fetch(`${API_BASE}/memory/clear`, { method: 'POST' });
      const data = await res.json();
      if (!res.ok || data.status !== 'success') throw new Error(data.message || 'clear failed');
      setStatus(lang === 'zh' ? '长期记忆已清空' : 'Long-term memory cleared');
      await loadRecords();
    } catch {
      setStatus(lang === 'zh' ? '清空失败' : 'Clear failed');
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-4xl flex-col rounded-lg border border-[var(--border)] bg-white shadow-2xl">
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-semibold text-[var(--text-main)]">
              <Database className="h-4 w-4 text-[var(--accent)]" />
              <span>{lang === 'zh' ? '长期记忆管理' : 'Memory Manager'}</span>
            </div>
            <div className="mt-1 text-xs text-[var(--text-muted)]">
              {lang === 'zh' ? `共 ${total} 条，当前显示 ${records.length} 条` : `${total} total, showing ${records.length}`}
            </div>
          </div>
          <button className="grid h-8 w-8 place-items-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)]" onClick={onClose} aria-label="Close memory manager">
            <X className="h-4 w-4" />
          </button>
        </div>

        <div className="flex shrink-0 flex-col gap-3 border-b border-[var(--border)] p-4 sm:flex-row sm:items-center">
          <div className="flex min-w-0 flex-1 items-center gap-2 rounded-lg border border-[var(--border)] bg-white px-3">
            <Search className="h-4 w-4 shrink-0 text-[var(--text-muted)]" />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') loadRecords();
              }}
              className="h-10 min-w-0 flex-1 outline-none"
              placeholder={lang === 'zh' ? '搜索记忆文本、会话、owner、scope...' : 'Search text, session, owner, scope...'}
            />
          </div>
          <div className="flex shrink-0 gap-2">
            <button className="inline-flex h-10 items-center gap-2 rounded-lg border border-[var(--border)] px-3 text-sm hover:bg-[var(--bg-soft)]" onClick={loadRecords} disabled={loading}>
              <RefreshCcw className={`h-4 w-4 ${loading ? 'animate-spin' : ''}`} />
              {lang === 'zh' ? '刷新' : 'Refresh'}
            </button>
            <button className="inline-flex h-10 items-center gap-2 rounded-lg border border-[var(--danger-soft)] px-3 text-sm text-[var(--danger)] hover:bg-[var(--danger-soft)]" onClick={clearMemory}>
              <Eraser className="h-4 w-4" />
              {lang === 'zh' ? '清空记忆' : 'Clear memory'}
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4">
          {status ? <div className="mb-3 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{status}</div> : null}
          {records.length === 0 ? (
            <div className="rounded-lg border border-dashed border-[var(--border-strong)] p-8 text-center text-sm text-[var(--text-muted)]">
              {loading ? (lang === 'zh' ? '加载中...' : 'Loading...') : (lang === 'zh' ? '暂无长期记忆' : 'No long-term memory records')}
            </div>
          ) : (
            <div className="space-y-3">
              {records.map((record) => {
                const editing = editingId === record.id;
                return (
                  <div key={record.id} className="rounded-lg border border-[var(--border)] bg-white p-3 shadow-sm">
                    <div className="mb-2 flex flex-wrap items-center gap-2 text-xs text-[var(--text-muted)]">
                      <span className="max-w-full truncate font-mono text-[var(--text-main)]">{record.id}</span>
                      {record.session_id ? <span className="rounded-md bg-[var(--bg-soft)] px-2 py-0.5">{record.session_id}</span> : null}
                      {record.scope ? <span className="rounded-md bg-[var(--mint-soft)] px-2 py-0.5 text-[var(--mint-strong)]">{record.scope}</span> : null}
                      {record.created_at ? <span>{formatMemoryTime(record.created_at)}</span> : null}
                    </div>
                    {editing ? (
                      <textarea
                        value={draftText}
                        onChange={(event) => setDraftText(event.target.value)}
                        className="min-h-28 w-full resize-y rounded-lg border border-[var(--border)] p-3 text-sm leading-6 outline-none focus:border-[var(--accent)]"
                      />
                    ) : (
                      <div className="whitespace-pre-wrap break-words text-sm leading-6 text-[var(--text-main)]">{record.text}</div>
                    )}
                    <div className="mt-3 flex justify-end gap-2">
                      {editing ? (
                        <>
                          <button className="rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--bg-soft)]" onClick={() => setEditingId('')}>
                            {lang === 'zh' ? '取消' : 'Cancel'}
                          </button>
                          <button className="inline-flex items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 py-1.5 text-sm text-white hover:bg-[var(--accent-strong)]" onClick={() => saveEdit(record)}>
                            <Save className="h-3.5 w-3.5" />
                            {lang === 'zh' ? '保存' : 'Save'}
                          </button>
                        </>
                      ) : (
                        <>
                          <button className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 py-1.5 text-sm hover:bg-[var(--bg-soft)]" onClick={() => startEdit(record)}>
                            <Pencil className="h-3.5 w-3.5" />
                            {lang === 'zh' ? '修改' : 'Edit'}
                          </button>
                          <button className="inline-flex items-center gap-1.5 rounded-lg border border-[var(--danger-soft)] px-3 py-1.5 text-sm text-[var(--danger)] hover:bg-[var(--danger-soft)]" onClick={() => deleteRecord(record)}>
                            <Trash2 className="h-3.5 w-3.5" />
                            {lang === 'zh' ? '删除' : 'Delete'}
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function formatMemoryTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}

function ConfirmModal({ lang, req, onAction }: { lang: Lang; req: ConfirmReq; onAction: (action: 'approve' | 'deny') => void }) {
  return (
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4 backdrop-blur-sm">
      <div className="w-full max-w-2xl rounded-lg border border-[var(--border)] bg-white p-5 shadow-2xl">
        <div className="mb-4 flex items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--danger-soft)] text-[var(--danger)]">
            <ShieldCheck className="h-5 w-5" />
          </div>
          <div>
            <div className="font-semibold">{lang === 'zh' ? '操作确认' : 'Action Confirmation'}</div>
            <div className="text-sm text-[var(--text-muted)]">{lang === 'zh' ? '执行前请审核' : 'Review before execution'}</div>
          </div>
        </div>
        <p className="mb-4 whitespace-pre-wrap text-sm leading-6 text-[var(--text-main)]">{req.prompt}</p>
        {req.code_preview && (
          <div className="mb-5 max-h-72 overflow-auto rounded-lg border border-[var(--border)] bg-[#111827]">
            <SyntaxHighlighter language="python" style={oneDark} showLineNumbers customStyle={{ background: 'transparent', margin: 0, padding: '14px', fontSize: '12px' }}>
              {req.code_preview}
            </SyntaxHighlighter>
          </div>
        )}
        <div className="flex justify-end gap-2">
          <button className="rounded-lg border border-[var(--border)] bg-white px-4 py-2 text-sm font-medium text-[var(--text-main)] hover:bg-[var(--bg-soft)]" onClick={() => onAction('deny')}>
            {lang === 'zh' ? '拒绝' : 'Deny'}
          </button>
          <button className="rounded-lg bg-[var(--mint-strong)] px-4 py-2 text-sm font-medium text-white hover:bg-[var(--mint)]" onClick={() => onAction('approve')}>
            {lang === 'zh' ? '批准' : 'Approve'}
          </button>
        </div>
      </div>
    </div>
  );
}
