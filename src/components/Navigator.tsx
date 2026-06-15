// Navigator.tsx
import React, { useState } from "react";
import { Activity, Bot, ChevronDown, FileText, User } from "lucide-react";
import type { ConversationNavItem, Lang } from "../types.ts";

export function ConversationNavigator({
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



export function ConversationNavButton({
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



