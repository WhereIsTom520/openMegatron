import React, { useState, useEffect, useRef } from "react";
import {
  Bot, ChevronDown, Languages, LayoutDashboard, Menu,
  PanelRightClose, Send, Sparkles, Trash2, Zap,
} from "lucide-react";
import { quickPrompts } from "../types.ts";
import type { ConfigText, Lang, Message, ModelProviderOption, ModelSelection, SkillDraft } from "../types.ts";
import { getHashAnchor, getMessageAnchorId, getTraceAnchorId } from "../utils.tsx";
import { AgentMessage, UserMessage } from "./Messages.tsx";
import ChatSearch from "./ChatSearch.tsx";
import MultiSessionTabs from "./MultiSessionTabs.tsx";

function IconButton({ testId, title, onClick, children }: {
  testId?: string; title: string; onClick: () => void; children: React.ReactNode;
}) {
  return (
    <button data-testid={testId} title={title} onClick={onClick}
      className="grid h-8 w-8 place-items-center rounded-lg text-gray-400 transition hover:bg-white/10 hover:text-white">
      {children}
    </button>
  );
}

function ModelPicker({ lang, providers, selection, onSelect }: {
  lang: Lang; providers: ModelProviderOption[]; selection: ModelSelection; onSelect: (s: ModelSelection) => void;
}) {
  const active = providers.find(p => p.id === selection.provider);
  return (
    <div className="relative group">
      <button className="flex items-center gap-1.5 rounded-lg bg-white/5 px-3 py-1.5 text-xs text-gray-300 transition hover:bg-white/10">
        <Zap className="h-3 w-3 text-blue-400" />
        <span className="max-w-[100px] truncate">{active?.label || selection.model}</span>
        <ChevronDown className="h-3 w-3 text-gray-500" />
      </button>
      <div className="absolute right-0 top-full z-50 mt-1 hidden w-56 rounded-xl border border-white/10 bg-gray-900 p-1.5 shadow-2xl group-hover:block">
        {providers.map(p => (
          <div key={p.id}>
            <div className="px-2 py-1 text-[10px] font-semibold uppercase tracking-wider text-gray-500">{p.label}</div>
            {(p.models || []).map((m) => (
              <button key={m.id} onClick={() => onSelect({ provider: p.id, model: m.id })}
                className={`w-full truncate rounded-lg px-3 py-1.5 text-left text-xs transition ${
                  selection.provider === p.id && selection.model === m.id
                    ? 'bg-blue-500/20 text-blue-300' : 'text-gray-400 hover:bg-white/5 hover:text-gray-200'
                }`}>
                {m.name || m.id}
              </button>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function EmptyChat({ lang, onPrompt }: { lang: Lang; onPrompt: (v: string) => void }) {
  const zh = lang === 'zh';
  const prompts = quickPrompts[lang] || quickPrompts.en;
  return (
    <div className="flex flex-1 flex-col items-center justify-center px-6 py-16">
      <div className="mb-6 flex h-16 w-16 items-center justify-center rounded-2xl bg-gradient-to-br from-blue-500 to-purple-600 shadow-lg shadow-blue-500/20">
        <Bot className="h-8 w-8 text-white" />
      </div>
      <h2 className="mb-2 text-2xl font-bold tracking-tight text-white">
        {zh ? '今天想做什么？' : 'What can I help with?'}
      </h2>
      <p className="mb-10 max-w-md text-center text-sm leading-relaxed text-gray-400">
        {zh
          ? '30+ 工具已就绪。代码执行、RAG 检索、GUI 自动化、PPT 制作——直接说需求即可。'
          : '30+ tools ready. Code, RAG search, GUI automation, PPT generation — just ask.'}
      </p>
      <div className="grid w-full max-w-xl grid-cols-2 gap-2.5">
        {prompts.slice(0, 6).map((p: string, i: number) => (
          <button key={i} onClick={() => onPrompt(p)}
            className="rounded-xl border border-white/10 bg-white/5 px-4 py-3 text-left text-sm text-gray-300 transition hover:border-white/20 hover:bg-white/10 hover:text-white">
            <Sparkles className="mb-1.5 h-3.5 w-3.5 text-blue-400" />
            {p}
          </button>
        ))}
      </div>
    </div>
  );
}

export function MainChat({
  t, lang, messages, activeProjectTitle, activeConversationTitle,
  members, activeMemberId, isChatLoading, backendStarting, skillDraft,
  modelProviders, modelSelection, onSelectModel,
  onSelectMember, onAddMember, onPrompt, onSendMessage, onClearHistory,
  onToggleLang, onToggleLeft, onToggleRight,
  conversations, activeConversationId, onCreateConversation,
  onDeleteConversation, onRenameConversation, onSwitchConversation,
  chatSearchMessages, setChatSearchScrollTo,
}: {
  t: ConfigText; lang: Lang; messages: Message[];
  activeProjectTitle: string; activeConversationTitle: string;
  members?: any[]; activeMemberId?: string;
  isChatLoading: boolean; backendStarting?: boolean;
  skillDraft: SkillDraft | null;
  modelProviders: ModelProviderOption[]; modelSelection: ModelSelection;
  onSelectModel: (s: ModelSelection) => void;
  onSelectMember?: (id: string) => void;
  onAddMember?: () => void;
  onPrompt: (v: string) => void;
  onSendMessage: (v: string) => void;
  onClearHistory: () => void;
  onToggleLang: () => void;
  onToggleLeft: () => void;
  onToggleRight: () => void;
  conversations?: any[]; activeConversationId?: string;
  onCreateConversation?: () => void;
  onDeleteConversation?: (id: string) => void;
  onRenameConversation?: (id: string, title: string) => void;
  onSwitchConversation?: (id: string) => void;
  chatSearchMessages?: Message[];
  setChatSearchScrollTo?: (id: string | null) => void;
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

  const tabData = (conversations || []).map((c: any) => ({
    id: c.id,
    title: c.title,
    isActive: c.id === activeConversationId,
    messageCount: c.messages?.length || 0,
  }));

  return (
    <main className="flex min-w-0 flex-1 flex-col bg-gray-950">
      {/* Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b border-white/5 bg-gray-950 px-4">
        <div className="flex items-center gap-3">
          <button onClick={onToggleLeft}
            className="grid h-8 w-8 place-items-center rounded-lg text-gray-500 transition hover:bg-white/5 hover:text-gray-300 md:hidden">
            <Menu className="h-4 w-4" />
          </button>
          <div className="hidden h-8 w-8 place-items-center rounded-lg bg-gradient-to-br from-blue-500 to-purple-600 sm:grid">
            <Bot className="h-4 w-4 text-white" />
          </div>
          <div>
            <div className="text-sm font-medium text-white">{lang === 'zh' ? 'OpenMegatron' : 'OpenMegatron'}</div>
          </div>
        </div>
        <div className="flex items-center gap-1">
          {chatSearchMessages && setChatSearchScrollTo && (
            <ChatSearch messages={chatSearchMessages} onJumpToMessage={(id) => setChatSearchScrollTo(id)} />
          )}
          <ModelPicker lang={lang} providers={modelProviders} selection={modelSelection} onSelect={onSelectModel} />
          <div className="mx-1 h-5 w-px bg-white/10" />
          <IconButton title={lang === 'zh' ? '清空历史' : 'Clear'} onClick={onClearHistory}>
            <Trash2 className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton title={lang === 'zh' ? '切换语言' : 'Language'} onClick={onToggleLang}>
            <Languages className="h-3.5 w-3.5" />
          </IconButton>
          <IconButton title={lang === 'zh' ? '运行状态' : 'Runtime'} onClick={onToggleRight}>
            <PanelRightClose className="h-3.5 w-3.5" />
          </IconButton>
        </div>
      </header>

      {/* Tabs */}
      {tabData.length > 0 && (
        <MultiSessionTabs
          tabs={tabData} activeId={activeConversationId || ''}
          onSwitch={(id) => onSwitchConversation?.(id)}
          onNew={() => onCreateConversation?.()}
          onClose={(id) => onDeleteConversation?.(id)}
          onRename={(id, title) => onRenameConversation?.(id, title)}
        />
      )}

      {/* Backend starting indicator */}
      {backendStarting && (
        <div className="flex items-center gap-2 border-b border-amber-500/20 bg-amber-500/5 px-4 py-2 text-xs text-amber-400">
          <span className="h-1.5 w-1.5 animate-pulse rounded-full bg-amber-400" />
          {lang === 'zh' ? '后端正在启动，请稍候...' : 'Backend starting, please wait...'}
        </div>
      )}

      {/* Messages */}
      <div className="min-h-0 flex-1 overflow-y-auto">
        {messages.length === 0 ? (
          <EmptyChat lang={lang} onPrompt={onPrompt} />
        ) : (
          <div className="mx-auto flex max-w-3xl flex-col gap-1 px-4 py-6">
            {messages.map((msg) =>
              msg.role === 'user' ? (
                <UserMessage key={msg.id} anchorId={getMessageAnchorId(msg.id)} content={msg.content} />
              ) : (
                <AgentMessage key={msg.id} lang={lang} anchorId={getMessageAnchorId(msg.id)}
                  traceAnchorId={getTraceAnchorId(msg.id)} thoughts={msg.thoughts} isStreaming={msg.isStreaming}>
                  {msg.content}
                </AgentMessage>
              ),
            )}
            <div ref={messagesEndRef} className="h-1" />
          </div>
        )}
      </div>

      {/* Input */}
      <div className="shrink-0 border-t border-white/5 bg-gray-950 px-4 py-4">
        <div className="mx-auto max-w-3xl">
          <div className="flex items-end gap-2 rounded-2xl border border-white/10 bg-white/5 px-4 py-3 transition focus-within:border-blue-500/50 focus-within:bg-white/[0.07]">
            <textarea
              ref={textareaRef}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
              }}
              placeholder={t.inputPlace}
              rows={1}
              disabled={isChatLoading}
              className="max-h-40 min-h-6 flex-1 resize-none bg-transparent text-sm leading-6 text-white outline-none placeholder:text-gray-500"
            />
            <button
              onClick={submit}
              disabled={isChatLoading || !inputValue.trim()}
              className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-blue-500 text-white transition hover:bg-blue-400 disabled:cursor-not-allowed disabled:bg-white/10 disabled:text-gray-600"
              title={lang === 'zh' ? '发送' : 'Send'}>
              <Send className="h-4 w-4" />
            </button>
          </div>
        </div>
      </div>
    </main>
  );
}
