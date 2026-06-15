// MemoryModal.tsx
import React, { useCallback, useEffect, useState } from "react";
import { Database, Eraser, Pencil, RefreshCcw, Save, Search, Share2, Trash2, X } from "lucide-react";
import { CitationGraph, parseCitationMermaid } from "./citation-graph/index.tsx";
import { API_BASE } from "../types.ts";
import type { Lang, MemoryRecord, MemoryRecordsResponse } from "../types.ts";

export function guessCodeLanguage(pathValue: string) {
  if (/\.tsx?$/i.test(pathValue)) return 'tsx';
  if (/\.jsx?$/i.test(pathValue)) return 'javascript';
  if (/\.py$/i.test(pathValue)) return 'python';
  if (/\.ya?ml$/i.test(pathValue)) return 'yaml';
  if (/\.json$/i.test(pathValue)) return 'json';
  if (/\.md$/i.test(pathValue)) return 'markdown';
  return 'text';
}



export function MemoryManagerModal({ lang, onClose }: { lang: Lang; onClose: () => void }) {
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



export function formatMemoryTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString();
}



