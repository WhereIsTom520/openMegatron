// ConfirmModal.tsx
import React from "react";
import { ShieldCheck } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import type { ConfirmReq, Lang } from "../types.ts";

export function ConfirmModal({ lang, req, onAction }: { lang: Lang; req: ConfirmReq; onAction: (action: 'approve' | 'deny') => void }) {
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


