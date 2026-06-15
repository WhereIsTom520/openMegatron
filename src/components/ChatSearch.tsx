import React, { useState, useMemo, useCallback } from 'react';
import { Search, X } from 'lucide-react';

interface Message {
  id: string;
  role: 'user' | 'assistant';
  content: string;
  userId?: string;
  userName?: string;
}

interface ChatSearchProps {
  messages: Message[];
  onJumpToMessage: (messageId: string) => void;
}

export default function ChatSearch({ messages, onJumpToMessage }: ChatSearchProps) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);

  const results = useMemo(() => {
    if (!query.trim()) return [];
    const q = query.toLowerCase();
    return messages
      .filter(m => m.content.toLowerCase().includes(q))
      .map(m => ({
        ...m,
        preview: m.content.slice(
          Math.max(0, m.content.toLowerCase().indexOf(q) - 40),
          m.content.toLowerCase().indexOf(q) + q.length + 80
        ),
        matchIndex: m.content.toLowerCase().indexOf(q),
      }))
      .slice(0, 20);
  }, [query, messages]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      setOpen(false);
      setQuery('');
    }
    if ((e.ctrlKey || e.metaKey) && e.key === 'f') {
      e.preventDefault();
      setOpen(true);
    }
  }, []);

  if (!open) {
    return (
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-400 hover:text-gray-200 transition-colors"
        title="Search conversations (Ctrl+F)"
      >
        <Search className="w-3.5 h-3.5" />
        <span>Search</span>
      </button>
    );
  }

  return (
    <div className="relative" onKeyDown={handleKeyDown}>
      <div className="flex items-center gap-2 bg-gray-800 rounded-lg px-3 py-1.5">
        <Search className="w-4 h-4 text-gray-400" />
        <input
          type="text"
          value={query}
          onChange={e => setQuery(e.target.value)}
          placeholder="Search messages..."
          className="bg-transparent text-sm text-white placeholder-gray-500 outline-none w-48"
          autoFocus
        />
        <button
          onClick={() => { setOpen(false); setQuery(''); }}
          className="text-gray-500 hover:text-gray-300"
        >
          <X className="w-4 h-4" />
        </button>
      </div>

      {results.length > 0 && (
        <div className="absolute top-full mt-1 left-0 right-0 bg-gray-800 border border-gray-700 rounded-lg shadow-xl max-h-64 overflow-y-auto z-50">
          {results.map(r => (
            <button
              key={r.id}
              onClick={() => { onJumpToMessage(r.id); setOpen(false); setQuery(''); }}
              className="w-full text-left px-3 py-2 hover:bg-gray-700 border-b border-gray-700/50 last:border-0"
            >
              <span className="text-xs text-gray-500 mr-2">{r.role}:</span>
              <span className="text-sm text-gray-300">
                {r.preview.slice(0, 40)}
                <mark className="bg-yellow-500/30 text-yellow-200 rounded px-0.5">
                  {r.preview.slice(40, 40 + query.length)}
                </mark>
                {r.preview.slice(40 + query.length)}
              </span>
            </button>
          ))}
        </div>
      )}

      {query && results.length === 0 && (
        <div className="absolute top-full mt-1 left-0 right-0 bg-gray-800 border border-gray-700 rounded-lg shadow-xl p-3 text-sm text-gray-500 z-50">
          No results found
        </div>
      )}
    </div>
  );
}
