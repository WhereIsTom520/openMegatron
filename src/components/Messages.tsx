// Messages.tsx
import React, { useMemo } from "react";
import { Activity, Bot, Loader2, User } from "lucide-react";
import type { Lang } from "../types.ts";
import { splitRichTextBlocks } from "../utils.tsx";
import { CodeBlock, renderMarkdownText } from "./Renderers.tsx";
import { CitationGraph, parseCitationMermaid } from "./citation-graph/index.tsx";

export function UserMessage({ anchorId, content }: { anchorId: string; content: string }) {
  return (
    <div id={anchorId} data-chat-anchor className="chat-anchor flex justify-end">
      <div className="flex max-w-[88%] items-start gap-3">
        <div className="rounded-lg bg-[var(--accent)] px-4 py-3 text-sm leading-6 text-white shadow-sm">
          <TextBlock content={content} inverted />
        </div>
        <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--bg-soft)] text-[var(--text-muted)]">
          <User className="h-4 w-4" />
        </div>
      </div>
    </div>
  );
}



export function AgentMessage({
  anchorId,
  lang,
  traceAnchorId,
  children,
  thoughts,
  isStreaming,
}: {
  anchorId: string;
  lang: Lang;
  traceAnchorId: string;
  children: string;
  thoughts?: string[];
  isStreaming?: boolean;
}) {
  const hasContent = children.trim().length > 0;
  const currentThought = thoughts?.[thoughts.length - 1] || '';

  return (
    <div id={anchorId} data-chat-anchor className="chat-anchor flex items-start gap-3">
      <div className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-[var(--bg-rail)] text-white">
        <Bot className="h-4 w-4" />
      </div>
      <div className="min-w-0 flex-1">
        <div className="mb-1 flex items-center gap-2 text-sm font-semibold">
          <span>YuanGe AI</span>
          <span className="rounded-md bg-[var(--mint-soft)] px-2 py-0.5 text-xs font-medium text-[var(--mint-strong)]">Agent</span>
        </div>
        {thoughts && thoughts.length > 0 && (
          <div id={traceAnchorId} data-chat-anchor className="chat-anchor mb-3 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] p-3 font-mono text-xs text-[var(--text-muted)]">
            <div className="mb-2 flex items-center gap-2 text-[var(--text-main)]">
              <Activity className="h-3.5 w-3.5 text-[var(--accent)]" />
              <span>{lang === 'zh' ? '轨迹' : 'Trace'}</span>
            </div>
            <div className="space-y-1.5">
              {thoughts.map((thought, idx) => (
                <div key={`${thought}-${idx}`} className="flex gap-2">
                  <span className="text-[var(--mint-strong)]">•</span>
                  <span className="break-words">{thought}</span>
                </div>
              ))}
              {isStreaming && <div className="text-[var(--accent)]">{lang === 'zh' ? '处理中...' : 'processing...'}</div>}
            </div>
          </div>
        )}
        <div className="rounded-lg border border-[var(--border)] bg-white px-4 py-3 text-sm leading-6 text-[var(--text-main)] shadow-sm">
          {hasContent ? <TextBlock content={children} /> : isStreaming ? <RunningProgress lang={lang} currentThought={currentThought} /> : <TextBlock content={children} />}
          {isStreaming && hasContent && <span className="ml-1 inline-block h-4 w-1.5 translate-y-0.5 animate-pulse rounded-sm bg-[var(--accent)]" />}
        </div>
      </div>
    </div>
  );
}

function RunningProgress({ lang, currentThought }: { lang: Lang; currentThought: string }) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-[var(--accent)]">
        <Loader2 className="h-4 w-4 animate-spin" />
        <span className="font-semibold">{lang === 'zh' ? '智能体正在执行' : 'Agent is working'}</span>
      </div>
      <div className="rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-xs leading-5 text-[var(--text-muted)]">
        {currentThought || (lang === 'zh' ? '正在等待后端阶段性进度...' : 'Waiting for backend progress...')}
      </div>
      <div className="space-y-2" aria-hidden="true">
        <div className="h-2 w-11/12 animate-pulse rounded-full bg-[var(--bg-soft)]" />
        <div className="h-2 w-8/12 animate-pulse rounded-full bg-[var(--bg-soft)]" />
        <div className="h-2 w-10/12 animate-pulse rounded-full bg-[var(--bg-soft)]" />
      </div>
    </div>
  );
}



const MemoMermaidGraph = React.memo(function _MermaidGraph({ mermaidStr }: { mermaidStr: string }) {
  const { nodes, edges } = useMemo(() => parseCitationMermaid(mermaidStr), [mermaidStr]);
  if (nodes.length === 0) return <CodeBlock language="mermaid" content={mermaidStr} />;
  return <CitationGraph nodes={nodes} edges={edges} />;
});

export function TextBlock({ content, inverted = false }: { content: string; inverted?: boolean }) {
  const blocks = useMemo(() => splitRichTextBlocks(content), [content]);
  if (!content) return null;

  return (
    <div className="rich-text min-w-0 break-words">
      {blocks.map((block, idx) =>
        block.type === 'code' ? (
          <CodeBlock key={`code-${idx}`} language={block.language} content={block.content} />
        ) : (
          <React.Fragment key={`text-${idx}`}>{renderMarkdownText(block.content, `text-${idx}`, inverted)}</React.Fragment>
        ),
      )}
    </div>
  );
}



