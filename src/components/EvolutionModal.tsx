// EvolutionModal.tsx
import React, { useCallback, useEffect, useState } from "react";
import { RefreshCcw, Save, ShieldCheck, X } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { API_BASE } from "../types.ts";
import type { EvolutionProposal, EvolutionProposalsResponse, Lang } from "../types.ts";
import { formatMemoryTime, guessCodeLanguage } from "./MemoryModal.tsx";

export function EvolutionManagerModal({ lang, onClose }: { lang: Lang; onClose: () => void }) {
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



export function EvolutionProposalCard({
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



