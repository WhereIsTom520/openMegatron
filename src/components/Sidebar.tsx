// Sidebar.tsx
import React from "react";
import {
  Activity,
  Brain,
  ChevronDown,
  Clapperboard,
  Code2,
  Database,
  Eraser,
  FileText,
  Folder,
  MessageSquare,
  Plus,
  ShieldCheck,
  Trash2,
  X,
} from "lucide-react";
import { API_BASE } from "../types.ts";
import type { ChatConversation, ChatProject, ConfigText, Lang, SkillKey } from "../types.ts";
import { displayWorkspaceTitle, getIconComponent } from "../utils.tsx";

export function Sidebar({
  t,
  lang,
  projects,
  conversations,
  activeProjectId,
  activeConversationId,
  runningConversationIds,
  onSelectProject,
  onCreateProject,
  onDeleteProject,
  onSelectConversation,
  onCreateConversation,
  onDeleteConversation,
  onTestSkill,
  onOpenEvolutionManager,
  onOpenMemoryManager,
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
  onSelectProject: (projectId: string) => void;
  onCreateProject: () => void;
  onDeleteProject: (projectId: string) => void;
  onSelectConversation: (conversationId: string) => void;
  onCreateConversation: () => void;
  onDeleteConversation: (conversationId: string) => void;
  onTestSkill: (skill: SkillKey) => void;
  onOpenEvolutionManager: () => void;
  onOpenMemoryManager: () => void;
  onClearAllData: () => void;
  clearAllDisabled: boolean;
  dataActionMessage: string;
  onClose: () => void;
}) {
  const activeTasks = t.activeTasksList || [];
  const completedTasks = t.completedTasksList || [];
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
          {activeTasks.map((task, idx) => <NavItem key={`${task.name}-${idx}`} active icon={getIconComponent(task.icon)} text={task.name} />)}
        </div>
        <div className="mt-6">
          <SectionLabel>{t.completedTasks}</SectionLabel>
          <div className="space-y-1.5">
            {completedTasks.map((task, idx) => <NavItem key={`${task.name}-${idx}`} icon={getIconComponent(task.icon)} text={task.name} />)}
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



export function PanelHeader({ label, onAdd }: { label: string; onAdd: () => void }) {
  return (
    <div className="mb-2 flex items-center justify-between px-2 text-xs font-semibold text-white/45">
      <span>{label}</span>
      <button className="grid h-6 w-6 place-items-center rounded-md text-white/55 hover:bg-white/10 hover:text-white" type="button" onClick={onAdd} title={label}>
        <Plus className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}



export function SidebarButton({
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



export function SectionLabel({ children }: { children: React.ReactNode }) {
  return <div className="mb-2 px-2 text-xs font-semibold text-white/45">{children}</div>;
}



export function NavItem({ active, icon, text }: { active?: boolean; icon: React.ReactNode; text: string }) {
  return (
    <div className={`flex items-center gap-3 rounded-lg border px-3 py-2.5 text-sm ${active ? 'border-white/14 bg-white/10 text-white' : 'border-transparent text-white/60 hover:bg-white/[0.06] hover:text-white'}`}>
      <span className={active ? 'text-[var(--mint)]' : 'text-white/45'}>{icon}</span>
      <span className="min-w-0 truncate">{text}</span>
    </div>
  );
}



export function SkillChip({ icon, label, onClick }: { icon: React.ReactNode; label: string; onClick: () => void }) {
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



