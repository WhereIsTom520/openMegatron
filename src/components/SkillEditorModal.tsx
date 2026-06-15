// SkillEditorModal.tsx
import React, { useCallback, useEffect, useState } from "react";
import { Eye, History, Pencil, RefreshCcw, RotateCcw, Save, X } from "lucide-react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";

let API_BASE =
  (import.meta as any).env?.VITE_API_BASE ||
  (typeof window !== "undefined" ? `${window.location.protocol}//${window.location.hostname}:8000` : "http://127.0.0.1:8000");

export interface SkillFile {
  name: string;
  path: string;
}

export interface SkillInfo {
  name: string;
  path: string;
  files: SkillFile[];
}

export interface RollbackEntry {
  id: string;
  skill_name: string;
  path: string;
  timestamp: number;
  original_content: string;
}

type TabKey = "source" | "edit" | "diff";

export function SkillEditorModal({ lang, onClose }: { lang: "zh" | "en"; onClose: () => void }) {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkill, setSelectedSkill] = useState("");
  const [selectedFile, setSelectedFile] = useState("");
  const [originalContent, setOriginalContent] = useState("");
  const [editedContent, setEditedContent] = useState("");
  const [activeTab, setActiveTab] = useState<TabKey>("source");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [skillFiles, setSkillFiles] = useState<SkillFile[]>([]);

  // Rollback state
  const [rollbacks, setRollbacks] = useState<RollbackEntry[]>([]);
  const [showRollbacks, setShowRollbacks] = useState(false);
  const [rollbackLoading, setRollbackLoading] = useState(false);

  // Load skill list on mount
  useEffect(() => {
    loadSkills();
  }, []);

  const loadSkills = async () => {
    setLoading(true);
    setStatus("");
    try {
      const res = await fetch(`${API_BASE}/skills/list`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok || data.status !== "success") throw new Error(data.message || `HTTP ${res.status}`);
      setSkills(data.skills || []);
    } catch (error) {
      setSkills([]);
      const message = error instanceof Error ? error.message : "";
      setStatus(message || (lang === "zh" ? "后端不可用，无法加载技能列表" : "Backend unavailable; cannot load skill list"));
    } finally {
      setLoading(false);
    }
  };

  // When a skill is selected, load its file list and first file content
  const handleSelectSkill = async (skillPath: string) => {
    setSelectedSkill(skillPath);
    setSelectedFile("");
    setOriginalContent("");
    setEditedContent("");
    setActiveTab("source");
    setShowRollbacks(false);

    const skill = skills.find((s) => s.path === skillPath);
    if (!skill) return;
    setSkillFiles(skill.files || []);

    // Auto-open the first SKILL.md file
    const firstMd = skill.files?.find((f) => f.name.toLowerCase() === "skill.md");
    if (firstMd) {
      setSelectedFile(firstMd.path);
      await loadFileContent(firstMd.path);
    }
  };

  const handleSelectFile = async (filePath: string) => {
    setSelectedFile(filePath);
    setActiveTab("source");
    setShowRollbacks(false);
    if (filePath) {
      await loadFileContent(filePath);
    }
  };

  const loadFileContent = async (filePath: string) => {
    setStatus("");
    setLoading(true);
    try {
      const res = await fetch(`${API_BASE}/skills/read?path=${encodeURIComponent(filePath)}`, { cache: "no-store" });
      const data = await res.json();
      if (!res.ok || data.status !== "success") throw new Error(data.message || `HTTP ${res.status}`);
      setOriginalContent(data.content || "");
      setEditedContent(data.content || "");
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      setStatus(message || (lang === "zh" ? "读取文件失败" : "Failed to read file"));
    } finally {
      setLoading(false);
    }
  };

  // Save with rollback
  const handleSave = async () => {
    if (!selectedFile || editedContent === originalContent) {
      setStatus(lang === "zh" ? "没有修改" : "No changes");
      return;
    }
    const ok = window.confirm(
      lang === "zh"
        ? `确认保存 ${selectedFile}？将自动创建回滚快照。`
        : `Save ${selectedFile}? A rollback snapshot will be created automatically.`
    );
    if (!ok) return;
    setStatus(lang === "zh" ? "正在保存..." : "Saving...");
    setLoading(true);
    try {
      const skillName = selectedSkill;
      const res = await fetch(`${API_BASE}/skills/write`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ path: selectedFile, content: editedContent, skill_name: skillName }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "success") throw new Error(data.message || "Write failed");
      setOriginalContent(editedContent);
      setStatus(lang === "zh" ? `已保存并创建快照 ${data.snapshot}` : `Saved with snapshot ${data.snapshot}`);
      setActiveTab("source");
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      setStatus(message || (lang === "zh" ? "保存失败" : "Save failed"));
    } finally {
      setLoading(false);
    }
  };

  // Load rollback list
  const loadRollbacks = async () => {
    setRollbackLoading(true);
    setStatus("");
    try {
      const res = await fetch(`${API_BASE}/skills/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "success") throw new Error(data.message || "Failed to load rollbacks");
      setRollbacks(data.rollbacks || []);
      setShowRollbacks(true);
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      setStatus(message || (lang === "zh" ? "加载回滚列表失败" : "Failed to load rollback list"));
    } finally {
      setRollbackLoading(false);
    }
  };

  // Execute rollback
  const handleRollback = async (snapshotId: string) => {
    const entry = rollbacks.find((r) => r.id === snapshotId);
    const label = entry ? entry.path : snapshotId;
    const ok = window.confirm(
      lang === "zh" ? `确认回滚 ${label}？将恢复到快照时的内容。` : `Rollback ${label}? Content will be restored to the snapshot version.`
    );
    if (!ok) return;
    setStatus(lang === "zh" ? "正在回滚..." : "Rolling back...");
    setRollbackLoading(true);
    try {
      const res = await fetch(`${API_BASE}/skills/rollback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ snapshot_id: snapshotId }),
      });
      const data = await res.json();
      if (!res.ok || data.status !== "success") throw new Error(data.message || "Rollback failed");
      setStatus(lang === "zh" ? "回滚成功" : "Rollback successful");
      // Refresh current file if it matches the rolled back file
      const rolledPath = entry?.path;
      if (rolledPath && rolledPath === selectedFile) {
        await loadFileContent(rolledPath);
      } else if (rolledPath) {
        setSelectedFile(rolledPath);
        await loadFileContent(rolledPath);
      }
      await loadRollbacks(); // refresh list
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      setStatus(message || (lang === "zh" ? "回滚失败" : "Rollback failed"));
    } finally {
      setRollbackLoading(false);
    }
  };

  // Diff view: simple line-based diff
  const diffLines = useSimpleDiff(originalContent, editedContent);

  const guessLang = (filename: string) => {
    const lower = filename.toLowerCase();
    if (lower.endsWith(".py")) return "python";
    if (lower.endsWith(".md")) return "markdown";
    if (lower.endsWith(".json")) return "json";
    if (lower.endsWith(".yaml") || lower.endsWith(".yml")) return "yaml";
    return "text";
  };

  const hasChanges = editedContent !== originalContent;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/45 p-4 backdrop-blur-sm">
      <div className="flex max-h-[88vh] w-full max-w-6xl flex-col rounded-lg border border-[var(--border)] bg-white shadow-2xl">
        {/* Header */}
        <div className="flex shrink-0 items-center justify-between border-b border-[var(--border)] px-5 py-4">
          <div className="min-w-0">
            <div className="flex items-center gap-2 font-semibold text-[var(--text-main)]">
              <Pencil className="h-4 w-4 text-[var(--accent)]" />
              <span>{lang === "zh" ? "技能编辑器" : "Skill Editor"}</span>
            </div>
            <div className="mt-1 text-xs text-[var(--text-muted)]">
              {lang === "zh"
                ? "编辑技能文件，保存时自动创建回滚快照"
                : "Edit skill files; rollback snapshots are created on save"}
            </div>
          </div>
          <button
            className="grid h-8 w-8 place-items-center rounded-lg text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)]"
            onClick={onClose}
            aria-label="Close skill editor"
          >
            <X className="h-4 w-4" />
          </button>
        </div>

        {/* Toolbar: skill dropdown, file dropdown, tabs, actions */}
        <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-[var(--border)] px-5 py-3">
          {/* Skill dropdown */}
          <select
            value={selectedSkill}
            onChange={(e) => handleSelectSkill(e.target.value)}
            className="h-9 rounded-lg border border-[var(--border)] bg-white px-3 text-sm outline-none focus:border-[var(--accent)]"
            disabled={loading || skills.length === 0}
          >
            <option value="">{lang === "zh" ? "选择技能..." : "Select skill..."}</option>
            {skills.map((skill) => (
              <option key={skill.path} value={skill.path}>
                {skill.name}
              </option>
            ))}
          </select>

          {/* File dropdown (only visible after skill selected) */}
          {skillFiles.length > 0 && (
            <select
              value={selectedFile}
              onChange={(e) => handleSelectFile(e.target.value)}
              className="h-9 min-w-0 rounded-lg border border-[var(--border)] bg-white px-3 font-mono text-xs outline-none focus:border-[var(--accent)]"
              disabled={loading}
            >
              <option value="">{lang === "zh" ? "选择文件..." : "Select file..."}</option>
              {skillFiles.map((f) => (
                <option key={f.path} value={f.path}>
                  {f.name}
                </option>
              ))}
            </select>
          )}

          <div className="ml-auto flex items-center gap-2">
            {/* Tab buttons */}
            <button
              className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${activeTab === "source" ? "border-[var(--accent)] bg-[var(--accent)]/8 text-[var(--accent)]" : "border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--bg-soft)]"}`}
              onClick={() => setActiveTab("source")}
              disabled={!selectedFile}
            >
              <Eye className="h-3.5 w-3.5" />
              {lang === "zh" ? "源文件" : "Source"}
            </button>
            <button
              className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${activeTab === "edit" ? "border-[var(--accent)] bg-[var(--accent)]/8 text-[var(--accent)]" : "border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--bg-soft)]"}`}
              onClick={() => setActiveTab("edit")}
              disabled={!selectedFile}
            >
              <Pencil className="h-3.5 w-3.5" />
              {lang === "zh" ? "编辑" : "Edit"}
            </button>
            <button
              className={`inline-flex h-8 items-center gap-1.5 rounded-lg border px-3 text-xs ${activeTab === "diff" ? "border-[var(--accent)] bg-[var(--accent)]/8 text-[var(--accent)]" : "border-[var(--border)] text-[var(--text-muted)] hover:bg-[var(--bg-soft)]"}`}
              onClick={() => setActiveTab("diff")}
              disabled={!selectedFile || !hasChanges}
            >
              <RefreshCcw className="h-3.5 w-3.5" />
              {lang === "zh" ? "差异" : "Diff"}
              {hasChanges ? <span className="ml-0.5 grid h-4 min-w-4 place-items-center rounded-full bg-[var(--amber)] px-1 text-[10px] font-bold text-white">!</span> : null}
            </button>

            <span className="mx-1 h-5 border-l border-[var(--border)]" />

            {/* Save button */}
            <button
              className="inline-flex h-8 items-center gap-1.5 rounded-lg bg-[var(--accent)] px-3 text-xs text-white hover:bg-[var(--accent-strong)] disabled:opacity-40"
              onClick={handleSave}
              disabled={!selectedFile || !hasChanges || loading}
            >
              <Save className="h-3.5 w-3.5" />
              {lang === "zh" ? "保存并快照" : "Save + snapshot"}
            </button>

            {/* Rollback button */}
            <button
              className="inline-flex h-8 items-center gap-1.5 rounded-lg border border-[var(--border)] px-3 text-xs hover:bg-[var(--bg-soft)]"
              onClick={() => {
                if (showRollbacks) {
                  setShowRollbacks(false);
                } else {
                  loadRollbacks();
                }
              }}
              disabled={rollbackLoading}
            >
              <History className="h-3.5 w-3.5" />
              {lang === "zh" ? "回滚" : "Rollback"}
            </button>
          </div>
        </div>

        {/* Status bar */}
        {status && (
          <div className="shrink-0 border-b border-[var(--border)] bg-[var(--bg-soft)] px-5 py-2 text-xs text-[var(--text-muted)]">
            {status}
          </div>
        )}

        {/* Rollback panel */}
        {showRollbacks && (
          <div className="shrink-0 border-b border-[var(--border)] bg-white p-4">
            <div className="mb-2 text-xs font-semibold text-[var(--text-main)]">
              {lang === "zh" ? `回滚快照 (${rollbacks.length})` : `Rollback snapshots (${rollbacks.length})`}
            </div>
            {rollbacks.length === 0 ? (
              <div className="text-xs text-[var(--text-muted)]">{lang === "zh" ? "无可用回滚" : "No rollbacks available"}</div>
            ) : (
              <div className="max-h-40 space-y-1.5 overflow-y-auto">
                {rollbacks.map((entry) => (
                  <div key={entry.id} className="flex items-center justify-between rounded-md border border-[var(--border)] px-3 py-2">
                    <div className="min-w-0 flex-1">
                      <div className="truncate font-mono text-xs text-[var(--text-main)]">{entry.path}</div>
                      <div className="text-[11px] text-[var(--text-muted)]">
                        {entry.skill_name} &middot; {new Date(entry.timestamp * 1000).toLocaleString()}
                      </div>
                    </div>
                    <button
                      className="ml-3 inline-flex h-7 shrink-0 items-center gap-1 rounded-md border border-[var(--border)] px-2 text-xs hover:bg-[var(--bg-soft)]"
                      onClick={() => handleRollback(entry.id)}
                      disabled={rollbackLoading}
                    >
                      <RotateCcw className="h-3 w-3" />
                      {lang === "zh" ? "恢复" : "Restore"}
                    </button>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}

        {/* Main content area */}
        <div className="min-h-0 flex-1 overflow-y-auto">
          {!selectedFile ? (
            <div className="flex h-full items-center justify-center p-8 text-sm text-[var(--text-muted)]">
              {loading
                ? `${lang === "zh" ? "加载中..." : "Loading..."}`
                : lang === "zh"
                  ? "从上方下拉菜单中选择一个技能和文件开始编辑"
                  : "Select a skill and file from the dropdowns above to start editing"}
            </div>
          ) : loading ? (
            <div className="flex h-full items-center justify-center p-8 text-sm text-[var(--text-muted)]">
              {lang === "zh" ? "加载中..." : "Loading..."}
            </div>
          ) : activeTab === "source" ? (
            <div className="bg-[#111827]">
              <SyntaxHighlighter
                language={guessLang(selectedFile)}
                style={oneDark}
                showLineNumbers
                customStyle={{ background: "transparent", margin: 0, padding: "16px", fontSize: "13px", minHeight: "320px" }}
                wrapLongLines
              >
                {originalContent}
              </SyntaxHighlighter>
            </div>
          ) : activeTab === "edit" ? (
            <textarea
              value={editedContent}
              onChange={(e) => setEditedContent(e.target.value)}
              className="h-full min-h-[320px] w-full resize-none border-0 bg-[#111827] p-4 font-mono text-[13px] leading-relaxed text-[#e5e7eb] outline-none placeholder:text-[#6b7280]"
              spellCheck={false}
              placeholder={lang === "zh" ? "编辑文件内容..." : "Edit file content..."}
            />
          ) : activeTab === "diff" ? (
            <div className="border-0 bg-[#111827] p-4 font-mono text-[13px] leading-relaxed" style={{ minHeight: "320px" }}>
              {diffLines.length === 0 ? (
                <span className="text-[#9ca3af]">{lang === "zh" ? "无差异" : "No differences"}</span>
              ) : (
                diffLines.map((line, i) => (
                  <div
                    key={i}
                    className="whitespace-pre-wrap break-all"
                    style={{
                      background: line.type === "add" ? "rgba(34,197,94,0.12)" : line.type === "remove" ? "rgba(239,68,68,0.12)" : "transparent",
                      color: line.type === "add" ? "#86efac" : line.type === "remove" ? "#fca5a5" : "#e5e7eb",
                    }}
                  >
                    {line.type === "add" ? "+ " : line.type === "remove" ? "- " : "  "}
                    {line.text}
                  </div>
                ))
              )}
            </div>
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** Simple line-based diff: returns an array of { type: "add"|"remove"|"same", text } */
function useSimpleDiff(original: string, edited: string) {
  return React.useMemo(() => {
    if (original === edited) return [];
    const origLines = original.split("\n");
    const editLines = edited.split("\n");
    const lines: { type: "add" | "remove" | "same"; text: string }[] = [];

    const lcs = buildLCS(origLines, editLines);
    let oi = 0, ei = 0, li = 0;

    while (oi < origLines.length || ei < editLines.length) {
      if (li < lcs.length && oi < origLines.length && ei < editLines.length && origLines[oi] === lcs[li] && editLines[ei] === lcs[li]) {
        lines.push({ type: "same", text: origLines[oi] });
        oi++; ei++; li++;
      } else if (oi < origLines.length && (li >= lcs.length || origLines[oi] !== lcs[li])) {
        lines.push({ type: "remove", text: origLines[oi] });
        oi++;
      } else if (ei < editLines.length && (li >= lcs.length || editLines[ei] !== lcs[li])) {
        lines.push({ type: "add", text: editLines[ei] });
        ei++;
      }
    }
    return lines;
  }, [original, edited]);
}

function buildLCS(a: string[], b: string[]): string[] {
  const m = a.length, n = b.length;
  const dp: number[][] = Array.from({ length: m + 1 }, () => new Array(n + 1).fill(0));
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      dp[i][j] = a[i] === b[j] ? 1 + dp[i + 1][j + 1] : Math.max(dp[i][j + 1], dp[i + 1][j]);
    }
  }
  const result: string[] = [];
  let i = 0, j = 0;
  while (i < m && j < n) {
    if (a[i] === b[j]) {
      result.push(a[i]);
      i++; j++;
    } else if (dp[i][j + 1] >= dp[i + 1][j]) {
      j++;
    } else {
      i++;
    }
  }
  return result;
}

export default SkillEditorModal;
