import React from "react";
import type { ElementType } from "react";
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
  Network,
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
} from "lucide-react";
import {
  DEFAULT_CONFIG,
  DEFAULT_CONVERSATION_TITLE,
  DEFAULT_MODEL_PROVIDERS,
  DEFAULT_PROJECT_TITLE,
  HISTORY_KEY,
  MODEL_SELECTION_KEY,
  WORKSPACE_KEY,
} from "./types.ts";
import type {
  ChatConversation,
  ChatProject,
  Config,
  ConversationNavItem,
  Lang,
  Message,
  ModelProviderOption,
  ModelSelection,
  RichTextBlock,
  WorkspaceState,
} from "./types.ts";

export const iconMap: Record<string, ElementType> = {
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
  Network,
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
};


export function getIconComponent(iconName: string, className = 'h-4 w-4') {
  const Icon = iconMap[iconName] ?? Activity;
  return <Icon className={className} />;
}

export function loadSavedMessages(): Message[] {
  try {
    const raw = localStorage.getItem(HISTORY_KEY);
    const value = raw ? JSON.parse(raw) : [];
    return Array.isArray(value) ? value : [];
  } catch {
    return [];
  }
}

export function createId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
}

export function createConversation(projectId: string, title = DEFAULT_CONVERSATION_TITLE, messages: Message[] = []): ChatConversation {
  const now = Date.now();
  return {
    id: createId('chat'),
    projectId,
    title,
    messages,
    createdAt: now,
    updatedAt: now,
  };
}

export function createWorkspaceState(legacyMessages: Message[] = []): WorkspaceState {
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

export function normalizeWorkspaceState(value: any): WorkspaceState | null {
  if (!value || typeof value !== 'object') return null;
  const projects = value.projects && typeof value.projects === 'object' ? value.projects : {};
  const conversations = value.conversations && typeof value.conversations === 'object' ? value.conversations : {};
  const activeProjectId = typeof value.activeProjectId === 'string' ? value.activeProjectId : '';
  const activeConversationId = typeof value.activeConversationId === 'string' ? value.activeConversationId : '';
  if (!projects[activeProjectId] || !conversations[activeConversationId]) return null;
  return { projects, conversations, activeProjectId, activeConversationId };
}

export function loadWorkspaceState(): WorkspaceState {
  try {
    const raw = localStorage.getItem(WORKSPACE_KEY);
    const parsed = raw ? normalizeWorkspaceState(JSON.parse(raw)) : null;
    if (parsed) return parsed;
  } catch {
    // Fall back to legacy single-chat history.
  }
  return createWorkspaceState(loadSavedMessages());
}

export function saveWorkspaceState(state: WorkspaceState) {
  localStorage.setItem(WORKSPACE_KEY, JSON.stringify(state));
}

export function deriveConversationTitle(messages: Message[]) {
  const firstUserMessage = messages.find((message) => message.role === 'user' && message.content.trim());
  if (!firstUserMessage) return DEFAULT_CONVERSATION_TITLE;
  return summarizeMessage(firstUserMessage.content, DEFAULT_CONVERSATION_TITLE);
}

export function getConversationSessionId(conversationId: string) {
  return `conversation_${toSafeAnchorPart(conversationId)}`;
}

export function mergeConfig(parsed: any): Config {
  return {
    zh: { ...DEFAULT_CONFIG.zh, ...(parsed?.zh || {}) },
    en: { ...DEFAULT_CONFIG.en, ...(parsed?.en || {}) },
  };
}

export function normalizeModelProviders(value?: ModelProviderOption[]) {
  const providers = Array.isArray(value) && value.length ? value : DEFAULT_MODEL_PROVIDERS;
  return providers
    .filter((provider) => provider?.id && provider?.label)
    .map((provider) => ({
      ...provider,
      models: Array.isArray(provider.models) && provider.models.length ? provider.models : [{ id: provider.id }],
    }));
}

export function defaultModelSelection(providers: ModelProviderOption[], preferredProvider?: string, preferredModel?: string): ModelSelection {
  const provider = providers.find((item) => item.id === preferredProvider) || providers[0] || DEFAULT_MODEL_PROVIDERS[0];
  const model = provider.models.find((item) => item.id === preferredModel) || provider.models[0] || { id: 'gpt-4o-mini' };
  return { provider: provider.id, model: model.id };
}

export function loadSavedModelSelection(): ModelSelection | null {
  try {
    const parsed = JSON.parse(localStorage.getItem(MODEL_SELECTION_KEY) || 'null');
    if (parsed?.provider && parsed?.model) return { provider: String(parsed.provider), model: String(parsed.model) };
  } catch {
    // Ignore malformed local model selection.
  }
  return null;
}

export function toSafeAnchorPart(value: string) {
  return value.replace(/[^a-zA-Z0-9_-]/g, '-');
}

export function getMessageAnchorId(messageId: string) {
  return `message-${toSafeAnchorPart(messageId)}`;
}

export function getTraceAnchorId(messageId: string) {
  return `trace-${toSafeAnchorPart(messageId)}`;
}

export function getHashAnchor() {
  if (typeof window === 'undefined' || !window.location.hash) return '';
  try {
    return decodeURIComponent(window.location.hash.slice(1));
  } catch {
    return window.location.hash.slice(1);
  }
}

export function setHashAnchor(anchorId: string) {
  const nextUrl = `${window.location.pathname}${window.location.search}#${encodeURIComponent(anchorId)}`;
  window.history.replaceState(null, '', nextUrl);
}

export function clearHashAnchor() {
  if (!window.location.hash) return;
  window.history.replaceState(null, '', `${window.location.pathname}${window.location.search}`);
}

export function scrollToAnchor(anchorId: string, updateHash = true) {
  const target = document.getElementById(anchorId);
  if (!target) return false;
  target.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (updateHash) setHashAnchor(anchorId);
  return true;
}

export function summarizeMessage(content: string, fallback: string) {
  const withoutCode = content.replace(/```[\s\S]*?```/g, ' ');
  const summary = withoutCode
    .replace(/[#*_>`|()[\]\[]/g, ' ')
    .replace(/\s+/g, ' ')
    .trim();
  if (!summary) return fallback;
  return summary.length > 64 ? `${summary.slice(0, 64)}...` : summary;
}

export function displayWorkspaceTitle(title: string, lang: Lang) {
  if (lang === 'zh') {
    if (title === DEFAULT_PROJECT_TITLE) return '本地工作区';
    if (title === DEFAULT_CONVERSATION_TITLE) return '新对话';
    const projectMatch = title.match(/^Project (\d+)$/);
    if (projectMatch) return `项目 ${projectMatch[1]}`;
  }
  return title;
}

export function buildConversationNavItems(messages: Message[], lang: Lang): ConversationNavItem[] {
  let userCount = 0;
  let assistantCount = 0;
  return messages.flatMap((message) => {
    if (message.role === 'user') {
      userCount += 1;
      return [
        {
          anchorId: getMessageAnchorId(message.id),
          label: lang === 'zh' ? `用户 ${userCount}` : `User ${userCount}`,
          summary: summarizeMessage(message.content, lang === 'zh' ? '用户消息' : 'User message'),
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

export function splitRichTextBlocks(content: string): RichTextBlock[] {
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

// ── API connection utilities ──────────────────────────

/** Backend ports to try in order when VITE_API_BASE is not explicitly set. */
const API_FALLBACK_PORTS = [8000, 8001, 8002, 8080, 3001];

/** Resolve the best candidate API base URL, probing if needed. */
export async function resolveApiBase(explicitBase?: string): Promise<string> {
  // Use explicit env var if provided
  if (explicitBase && explicitBase !== 'http://localhost:8000') {
    return explicitBase;
  }

  // Try the current hostname with each candidate port
  const hostname = typeof window !== 'undefined'
    ? window.location.hostname
    : '127.0.0.1';

  const candidates = explicitBase
    ? [explicitBase, ...API_FALLBACK_PORTS.map(p => `http://${hostname}:${p}`)]
    : API_FALLBACK_PORTS.map(p => `http://${hostname}:${p}`);

  // Deduplicate
  const unique = [...new Set(candidates)];

  // Quick probe: try each candidate with a short timeout
  for (const base of unique) {
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 1500);
      const resp = await fetch(`${base}/runtime_status`, {
        signal: controller.signal,
        mode: 'cors',
      });
      clearTimeout(timer);
      if (resp.ok) {
        const data = await resp.json();
        if (data?.ok) {
          console.log(`[openMegatron] Backend discovered at ${base} (PID ${data.backend?.pid}, ${data.skills?.loaded}/${data.skills?.total} skills)`);
          return base;
        }
      }
    } catch {
      // Port not responding — try next
    }
  }

  // Fallback to default
  console.warn('[openMegatron] No backend found on known ports, using default.');
  return explicitBase || `http://${hostname}:8000`;
}

/**
 * Retry a fetch with exponential backoff.
 * Returns the response if successful, or throws after maxRetries.
 */
export async function fetchWithRetry(
  input: RequestInfo,
  init?: RequestInit,
  maxRetries = 3,
  baseDelayMs = 500,
): Promise<Response> {
  let lastError: Error | null = null;
  for (let attempt = 0; attempt <= maxRetries; attempt++) {
    try {
      const response = await fetch(input, init);
      // Retry on 502/503/504 (gateway/transient errors)
      if (attempt < maxRetries && (response.status === 502 || response.status === 503 || response.status === 504)) {
        const delay = baseDelayMs * Math.pow(2, attempt);
        console.warn(`[openMegatron] Backend returned ${response.status}, retrying in ${delay}ms (${attempt + 1}/${maxRetries})...`);
        await new Promise(r => setTimeout(r, delay));
        continue;
      }
      return response;
    } catch (err: any) {
      lastError = err;
      if (attempt < maxRetries) {
        const delay = baseDelayMs * Math.pow(2, attempt);
        console.warn(`[openMegatron] Fetch failed, retrying in ${delay}ms (${attempt + 1}/${maxRetries}): ${err.message}`);
        await new Promise(r => setTimeout(r, delay));
      }
    }
  }
  throw lastError || new Error('Fetch failed after retries');
}

export type ConnectionState = 'connecting' | 'online' | 'degraded' | 'offline';

export interface ConnectionInfo {
  state: ConnectionState;
  latencyMs: number | null;
  error: string | null;
  backendInfo: {
    pid?: number;
    uptime?: number;
    skills?: number;
    services?: number;
  } | null;
}

