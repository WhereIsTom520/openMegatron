// Renderers.tsx
import React, { useCallback, useState } from "react";
import { AlertTriangle, CheckCircle2 } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { API_BASE, linkValidationCache } from "../types.ts";
import type { LinkValidationStatus, RichTextBlock } from "../types.ts";
import { scrollToAnchor } from "../utils.tsx";

export function splitRichTextBlocks(content: string): RichTextBlock[] {
  const blocks: RichTextBlock[] = [];
  let cursor = 0;

  while (cursor < content.length) {
    const fenceStart = content.indexOf('```', cursor);
    if (fenceStart === -1) {
      blocks.push({ type: 'text', content: content.slice(cursor) });
      break;
    }

    if (fenceStart > cursor) {
      blocks.push({ type: 'text', content: content.slice(cursor, fenceStart) });
    }

    const languageEnd = content.indexOf('\n', fenceStart + 3);
    if (languageEnd === -1) {
      blocks.push({ type: 'code', language: '', content: content.slice(fenceStart + 3) });
      break;
    }

    const language = content.slice(fenceStart + 3, languageEnd).trim();
    const fenceEnd = content.indexOf('```', languageEnd + 1);
    if (fenceEnd === -1) {
      blocks.push({ type: 'code', language, content: content.slice(languageEnd + 1) });
      break;
    }

    blocks.push({ type: 'code', language, content: content.slice(languageEnd + 1, fenceEnd).replace(/\n$/, '') });
    cursor = fenceEnd + 3;
  }

  return blocks.filter((block) => block.content.length > 0);
}



export function CodeBlock({ language, content }: { language?: string; content: string }) {
  return (
    <div className="my-3 max-w-full overflow-hidden rounded-lg border border-slate-700 bg-[#111827] text-left">
      <SyntaxHighlighter
        language={language || 'text'}
        style={oneDark}
        customStyle={{ background: 'transparent', margin: 0, padding: '14px', fontSize: '12px', lineHeight: 1.6 }}
        wrapLongLines
      >
        {content}
      </SyntaxHighlighter>
    </div>
  );
}



export function renderMarkdownText(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  const lines = text.replace(/\r\n/g, '\n').split('\n');
  const nodes: React.ReactNode[] = [];
  let index = 0;

  while (index < lines.length) {
    const line = lines[index];
    const trimmed = line.trim();

    if (!trimmed) {
      index += 1;
      continue;
    }

    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      nodes.push(
        React.createElement(
          `h${level + 1}`,
          {
            key: `${keyPrefix}-heading-${index}`,
            className: `${level <= 2 ? 'mt-4 text-base' : 'mt-3 text-sm'} mb-2 font-semibold leading-6 first:mt-0`,
          },
          renderInlineWithBreaks(heading[2], `${keyPrefix}-heading-${index}`, inverted),
        ),
      );
      index += 1;
      continue;
    }

    if (/^[-*_]{3,}$/.test(trimmed)) {
      nodes.push(<hr key={`${keyPrefix}-hr-${index}`} className="my-3 border-[var(--border)]" />);
      index += 1;
      continue;
    }

    if (isTableStart(lines, index)) {
      const tableLines = [lines[index], lines[index + 1]];
      index += 2;
      while (index < lines.length && lines[index].includes('|') && lines[index].trim()) {
        tableLines.push(lines[index]);
        index += 1;
      }
      nodes.push(renderTable(tableLines, `${keyPrefix}-table-${index}`, inverted));
      continue;
    }

    const listMatch = getListMatch(line);
    if (listMatch) {
      const ordered = /^\d/.test(listMatch.marker);
      const items: string[] = [];
      while (index < lines.length) {
        const currentMatch = getListMatch(lines[index]);
        if (!currentMatch || /^\d/.test(currentMatch.marker) !== ordered) break;
        items.push(currentMatch.content);
        index += 1;
      }
      const Tag = ordered ? 'ol' : 'ul';
      nodes.push(
        React.createElement(
          Tag,
          {
            key: `${keyPrefix}-list-${index}`,
            className: `${ordered ? 'list-decimal' : 'list-disc'} my-2 space-y-1 pl-5`,
          },
          items.map((item, itemIndex) => (
            <li key={`${keyPrefix}-list-${index}-${itemIndex}`}>{renderInlineWithBreaks(item, `${keyPrefix}-li-${index}-${itemIndex}`, inverted)}</li>
          )),
        ),
      );
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (index < lines.length && /^>\s?/.test(lines[index])) {
        quoteLines.push(lines[index].replace(/^>\s?/, ''));
        index += 1;
      }
      nodes.push(
        <blockquote key={`${keyPrefix}-quote-${index}`} className="my-2 border-l-2 border-[var(--border-strong)] pl-3 text-[var(--text-muted)]">
          {renderInlineWithBreaks(quoteLines.join('\n'), `${keyPrefix}-quote-${index}`, inverted)}
        </blockquote>,
      );
      continue;
    }

    const paragraphLines = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isMarkdownBlockStart(lines, index)) {
      paragraphLines.push(lines[index]);
      index += 1;
    }
    nodes.push(
      <p key={`${keyPrefix}-paragraph-${index}`} className="my-2 first:mt-0 last:mb-0">
        {renderInlineWithBreaks(paragraphLines.join('\n'), `${keyPrefix}-paragraph-${index}`, inverted)}
      </p>,
    );
  }

  return nodes;
}



export function isMarkdownBlockStart(lines: string[], index: number) {
  const line = lines[index] || '';
  return /^(#{1,6})\s+/.test(line) || /^[-*_]{3,}$/.test(line.trim()) || Boolean(getListMatch(line)) || /^>\s?/.test(line) || isTableStart(lines, index);
}



export function getListMatch(line: string) {
  const match = line.match(/^\s*(?<marker>[-*]|\d+[.)])\s+(?<content>.+)$/);
  if (!match?.groups) return null;
  return { marker: match.groups.marker, content: match.groups.content };
}



export function isTableStart(lines: string[], index: number) {
  return Boolean(lines[index]?.includes('|') && lines[index + 1] && isTableSeparator(lines[index + 1]));
}



export function isTableSeparator(line: string) {
  return /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$/.test(line);
}



export function splitTableRow(line: string) {
  return line
    .trim()
    .replace(/^\|/, '')
    .replace(/\|$/, '')
    .split('|')
    .map((cell) => cell.trim());
}



export function renderTable(tableLines: string[], key: string, inverted: boolean) {
  const headers = splitTableRow(tableLines[0]);
  const rows = tableLines.slice(2).map(splitTableRow);

  return (
    <div key={key} className="my-3 max-w-full overflow-x-auto rounded-lg border border-[var(--border)]">
      <table className="min-w-full border-collapse text-left text-sm">
        <thead className="bg-[var(--bg-soft)]">
          <tr>
            {headers.map((header, idx) => (
              <th key={`${key}-head-${idx}`} className="border-b border-[var(--border)] px-3 py-2 font-semibold">
                {renderInlineWithBreaks(header, `${key}-head-${idx}`, inverted)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((row, rowIndex) => (
            <tr key={`${key}-row-${rowIndex}`} className="border-t border-[var(--border)] first:border-t-0">
              {headers.map((_, cellIndex) => (
                <td key={`${key}-cell-${rowIndex}-${cellIndex}`} className="px-3 py-2 align-top">
                  {renderInlineWithBreaks(row[cellIndex] || '', `${key}-cell-${rowIndex}-${cellIndex}`, inverted)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}



export function renderInlineWithBreaks(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  return text.split('\n').flatMap((line, index, allLines) => {
    const rendered = renderInlineContent(line, `${keyPrefix}-line-${index}`, inverted);
    if (index === allLines.length - 1) return rendered;
    return [...rendered, <br key={`${keyPrefix}-br-${index}`} />];
  });
}



export function renderInlineContent(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  const parts = text.split(/(`[^`\n]*`)/g);
  const nodes: React.ReactNode[] = [];
  parts.forEach((part, idx) => {
    if (!part) return;
    if (/^`[^`\n]*`$/.test(part)) {
      nodes.push(
        <code key={`${keyPrefix}-code-${idx}`} className="rounded bg-black/10 px-1 py-0.5 font-mono text-[0.92em]">
          {part.slice(1, -1)}
        </code>
      );
      return;
    }
    nodes.push(...renderLinkedText(part, `${keyPrefix}-text-${idx}`, inverted));
  });
  return nodes;
}



export function renderLinkedText(text: string, keyPrefix: string, inverted: boolean): React.ReactNode[] {
  const tokenPattern = /\[[^\]\n]+\]\((?:https?:\/\/|www\.|#|\/|mailto:)[^\s)]*\)|(?:https?:\/\/|www\.)[^\s<]+|(?:doi:\s*)?10\.\d{4,9}\/[-._;()/:A-Z0-9]+/gi;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = tokenPattern.exec(text))) {
    if (match.index > lastIndex) {
      nodes.push(...renderDecoratedText(text.slice(lastIndex, match.index), `${keyPrefix}-plain-${lastIndex}`));
    }

    const token = match[0];
    const markdownLink = token.match(/^\[([^\]\n]+)\]\((.+)\)$/);
    if (markdownLink) {
      nodes.push(
        <RichLink key={`${keyPrefix}-link-${match.index}`} href={markdownLink[2]} inverted={inverted}>
          {markdownLink[1]}
        </RichLink>,
      );
    } else {
      const { value, trailing } = splitTrailingPunctuation(token);
      nodes.push(
        <RichLink key={`${keyPrefix}-link-${match.index}`} href={value} inverted={inverted}>
          {value}
        </RichLink>,
      );
      if (trailing) nodes.push(trailing);
    }

    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    nodes.push(...renderDecoratedText(text.slice(lastIndex), `${keyPrefix}-plain-${lastIndex}`));
  }

  return nodes;
}



export function renderDecoratedText(text: string, keyPrefix: string): React.ReactNode[] {
  const nodes: React.ReactNode[] = [];
  const boldPattern = /(\*\*[^*\n]+\*\*|__[^_\n]+__)/g;
  let lastIndex = 0;
  let match: RegExpExecArray | null;

  while ((match = boldPattern.exec(text))) {
    if (match.index > lastIndex) nodes.push(text.slice(lastIndex, match.index));
    nodes.push(
      <strong key={`${keyPrefix}-bold-${match.index}`} className="font-semibold">
        {match[0].slice(2, -2)}
      </strong>,
    );
    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) nodes.push(text.slice(lastIndex));
  return nodes;
}



export function splitTrailingPunctuation(token: string) {
  let value = token;
  let trailing = '';
  while (/[.,;:!?，。；：！？]$/.test(value)) {
    trailing = value.slice(-1) + trailing;
    value = value.slice(0, -1);
  }
  return { value, trailing };
}



export function normalizeHref(href: string) {
  const trimmed = href.trim();
  if (/^doi:\s*/i.test(trimmed)) return `https://doi.org/${trimmed.replace(/^doi:\s*/i, '')}`;
  if (/^10\.\d{4,9}\//i.test(trimmed)) return `https://doi.org/${trimmed}`;
  if (/^www\./i.test(trimmed)) return `https://${trimmed}`;
  return trimmed;
}



export function RichLink({ href, inverted, children }: { href: string; inverted: boolean; children: React.ReactNode }) {
  const normalizedHref = normalizeHref(href);
  const isHashLink = normalizedHref.startsWith('#');
  const isExternal = /^(https?:\/\/|mailto:)/i.test(normalizedHref);
  const shouldValidate = /^https?:\/\//i.test(normalizedHref);
  const [validation, setValidation] = useState<{ status: LinkValidationStatus; code?: number }>(() => linkValidationCache.get(normalizedHref) || { status: 'idle' });
  const className = inverted
    ? 'font-medium text-white underline decoration-white/60 underline-offset-2 hover:decoration-white'
    : 'font-medium text-[var(--accent-strong)] underline decoration-[var(--accent)]/35 underline-offset-2 hover:decoration-[var(--accent)]';

  const validateLink = useCallback(() => {
    if (!shouldValidate || validation.status === 'checking' || validation.status === 'ok' || validation.status === 'bad') return;
    const cached = linkValidationCache.get(normalizedHref);
    if (cached) {
      setValidation(cached);
      return;
    }
    const controller = new AbortController();
    setValidation({ status: 'checking' });
    fetch(`${API_BASE}/validate_link?url=${encodeURIComponent(normalizedHref)}`, { signal: controller.signal })
      .then((response) => response.json())
      .then((data) => {
        const next =
          data?.ok
            ? { status: 'ok' as const, code: data.status }
            : typeof data?.status === 'number' && data.status > 0
              ? { status: 'bad' as const, code: data.status }
              : { status: 'unknown' as const };
        linkValidationCache.set(normalizedHref, next);
        setValidation(next);
      })
      .catch(() => setValidation({ status: 'unknown' }));
  }, [normalizedHref, shouldValidate, validation.status]);

  return (
    <>
      <a
        href={normalizedHref}
        className={className}
        target={isExternal ? '_blank' : undefined}
        rel={isExternal ? 'noreferrer' : undefined}
        onMouseEnter={validateLink}
        onFocus={validateLink}
        onClick={(event) => {
          if (!isHashLink) return;
          event.preventDefault();
          const anchorId = normalizedHref.slice(1);
          scrollToAnchor(anchorId);
        }}
      >
        {children}
      </a>
      {shouldValidate && <LinkStatusBadge status={validation.status} code={validation.code} />}
    </>
  );
}



export function LinkStatusBadge({ status, code }: { status: LinkValidationStatus; code?: number }) {
  if (status === 'idle') return null;
  if (status === 'checking') {
    return <span className="ml-1 inline-block h-1.5 w-1.5 rounded-full bg-[var(--text-muted)] align-middle opacity-45" title="Checking link" />;
  }
  if (status === 'ok') {
    return <CheckCircle2 className="ml-1 inline-block h-3.5 w-3.5 align-[-2px] text-[var(--mint-strong)]" aria-label="Link available" />;
  }
  if (status === 'bad') {
    return (
      <span className="ml-1 inline-flex items-center gap-0.5 align-middle text-[var(--danger)]" title={`Link may be unavailable${code ? ` (${code})` : ''}`}>
        <AlertTriangle className="h-3.5 w-3.5" />
        {code ? <span className="font-mono text-[10px]">{code}</span> : null}
      </span>
    );
  }
  return <AlertTriangle className="ml-1 inline-block h-3.5 w-3.5 align-[-2px] text-[var(--amber)]" aria-label="Link not checked" />;
}



