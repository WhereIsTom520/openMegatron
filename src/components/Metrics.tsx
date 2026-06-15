// Metrics.tsx
import React from "react";

export function MetricBar({ icon, label, value, accent, detail }: { icon: React.ReactNode; label: string; value: number; accent: string; detail?: string }) {
  return (
    <div className="mb-4 last:mb-0">
      <div className="mb-2 flex items-center justify-between text-sm">
        <span className="flex min-w-0 items-center gap-2 text-[var(--text-muted)]">
          {icon}
          <span className="truncate">{label}</span>
        </span>
        <span className="font-mono text-[var(--text-main)]">{detail ?? `${value.toFixed(0)}%`}</span>
      </div>
      <div className="h-2 overflow-hidden rounded-full bg-[var(--bg-soft)]">
        <div className="h-full rounded-full transition-all duration-500" style={{ width: `${value}%`, background: accent }} />
      </div>
    </div>
  );
}



export function SmallMetric({ label, value, icon }: { label: string; value: string; icon: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
      <div className="mb-3 flex items-center justify-between text-[var(--text-muted)]">
        <span className="truncate text-sm">{label}</span>
        {icon}
      </div>
      <div className="font-mono text-2xl font-semibold text-[var(--text-main)]">{value}</div>
    </div>
  );
}



