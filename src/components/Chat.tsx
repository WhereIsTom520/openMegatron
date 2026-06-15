// Chat.tsx
import React, { useState, useEffect, useCallback, useMemo, useRef } from "react";
import {
  Bot,
  Brain,
  CheckCircle2,
  ChevronDown,
  Languages,
  LayoutDashboard,
  Menu,
  PanelRightClose,
  Send,
  Trash2,
} from "lucide-react";
import { quickPrompts } from "../types.ts";
import type { ConfigText, Lang, Message, ModelProviderOption, ModelSelection, SkillDraft } from "../types.ts";
import { getHashAnchor, getMessageAnchorId, getTraceAnchorId, toSafeAnchorPart } from "../utils.tsx";
import { AgentMessage, UserMessage } from "./Messages.tsx";

export function MainChat({
  t,
  lang,
  messages,
  activeProjectTitle,
  activeConversationTitle,
  isChatLoading,
  backendStarting,
  skillDraft,
  modelProviders,
  modelSelection,
  onSelectModel,
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
  isChatLoading: boolean;
  backendStarting?: boolean;
  skillDraft: SkillDraft | null;
  modelProviders: ModelProviderOption[];
  modelSelection: ModelSelection;
  onSelectModel: (selection: ModelSelection) => void;
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

      {backendStarting && (
        <div className="flex shrink-0 items-center gap-2 border-b border-[var(--border)] bg-[var(--accent)]/5 px-4 py-1.5 text-xs text-[var(--accent)]">
          <span className="inline-block h-1.5 w-1.5 animate-pulse rounded-full bg-[var(--accent)]" />
          {lang === "zh" ? "后端正在启动..." : "Backend is starting..."}
        </div>
      )}

      <div data-chat-scroll-container className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto flex max-w-4xl flex-col gap-5">
          {messages.length === 0 ? (
            <EmptyChat lang={lang} onPrompt={onPrompt} />
          ) : (
            messages.map((msg) =>
              msg.role === 'user' ? (
                <UserMessage key={msg.id} anchorId={getMessageAnchorId(msg.id)} content={msg.content} />
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



export function ModelPicker({
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



export function IconButton({ title, children, onClick, testId }: { title: string; children: React.ReactNode; onClick: () => void; testId?: string }) {
  return (
    <button data-testid={testId} className="grid h-8 w-8 place-items-center rounded-lg border border-[var(--border)] bg-white text-[var(--text-muted)] transition hover:border-[var(--border-strong)] hover:text-[var(--text-main)] sm:h-9 sm:w-9" title={title} onClick={onClick}>
      {children}
    </button>
  );
}



export function EmptyChat({ lang, onPrompt }: { lang: Lang; onPrompt: (value: string) => void }) {
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



