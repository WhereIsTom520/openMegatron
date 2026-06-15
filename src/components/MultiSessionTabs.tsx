import React, { useState, useCallback } from 'react';
import { Plus, MessageSquare, X, Edit2, Check } from 'lucide-react';

interface ConversationTab {
  id: string;
  title: string;
  isActive: boolean;
  messageCount: number;
}

interface MultiSessionTabsProps {
  tabs: ConversationTab[];
  activeId: string;
  onSwitch: (id: string) => void;
  onNew: () => void;
  onClose: (id: string) => void;
  onRename: (id: string, newTitle: string) => void;
}

export default function MultiSessionTabs({
  tabs, activeId, onSwitch, onNew, onClose, onRename,
}: MultiSessionTabsProps) {
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editTitle, setEditTitle] = useState('');

  const startRename = useCallback((tab: ConversationTab) => {
    setEditingId(tab.id);
    setEditTitle(tab.title);
  }, []);

  const commitRename = useCallback((id: string) => {
    if (editTitle.trim()) {
      onRename(id, editTitle.trim());
    }
    setEditingId(null);
  }, [editTitle, onRename]);

  if (tabs.length <= 1 && !tabs[0]?.messageCount) return null;

  return (
    <div className="flex items-center gap-0.5 px-2 py-1 bg-gray-900 border-b border-gray-800 overflow-x-auto scrollbar-thin">
      {tabs.map(tab => (
        <div
          key={tab.id}
          className={`flex items-center gap-1 px-3 py-1.5 rounded-t-lg text-xs cursor-pointer transition-colors shrink-0 max-w-[180px] ${
            tab.id === activeId
              ? 'bg-gray-800 text-white border-t border-l border-r border-gray-700'
              : 'text-gray-500 hover:text-gray-300 hover:bg-gray-800/50'
          }`}
          onClick={() => onSwitch(tab.id)}
        >
          <MessageSquare className="w-3 h-3 shrink-0" />
          {editingId === tab.id ? (
            <input
              type="text"
              value={editTitle}
              onChange={e => setEditTitle(e.target.value)}
              onBlur={() => commitRename(tab.id)}
              onKeyDown={e => {
                if (e.key === 'Enter') commitRename(tab.id);
                if (e.key === 'Escape') setEditingId(null);
              }}
              className="bg-gray-700 text-white text-xs outline-none px-1 rounded w-20"
              autoFocus
              onClick={e => e.stopPropagation()}
            />
          ) : (
            <span
              className="truncate"
              onDoubleClick={() => startRename(tab)}
              title={`${tab.title} (${tab.messageCount} messages) — double-click to rename`}
            >
              {tab.title}
            </span>
          )}
          {tab.messageCount > 0 && (
            <span className="text-[10px] text-gray-600 ml-0.5">{tab.messageCount}</span>
          )}
          {tabs.length > 1 && (
            <button
              onClick={e => { e.stopPropagation(); onClose(tab.id); }}
              className="ml-0.5 text-gray-600 hover:text-red-400 transition-colors"
            >
              <X className="w-3 h-3" />
            </button>
          )}
        </div>
      ))}

      <button
        onClick={onNew}
        className="flex items-center gap-1 px-2 py-1.5 rounded-lg text-xs text-gray-500 hover:text-gray-300 hover:bg-gray-800 transition-colors shrink-0"
        title="New conversation"
      >
        <Plus className="w-3.5 h-3.5" />
      </button>
    </div>
  );
}
