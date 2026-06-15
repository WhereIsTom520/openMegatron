// RuntimePanel.tsx
import React, { useEffect, useMemo, useState } from "react";
import { Activity, Code2, Database, Globe2, Server, ShieldCheck, X } from "lucide-react";
import { API_BASE } from "../types.ts";
import type { ConfigText, ConversationNavItem, Lang, RuntimeServiceStatus, RuntimeStatus } from "../types.ts";
import { ConversationNavigator } from "./Navigator.tsx";
import { MetricBar, SmallMetric } from "./Metrics.tsx";

export function clampPercent(value?: number | null) {
  if (typeof value !== 'number' || Number.isNaN(value)) return 0;
  return Math.max(0, Math.min(100, value));
}



export function formatUptime(seconds?: number) {
  if (typeof seconds !== 'number' || Number.isNaN(seconds) || seconds < 0) return '--';
  const days = Math.floor(seconds / 86400);
  const hours = Math.floor((seconds % 86400) / 3600);
  const minutes = Math.floor((seconds % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${Math.max(0, minutes)}m`;
}



export function formatUpdatedAt(timestamp: number | undefined, lang: Lang) {
  if (!timestamp) return lang === 'zh' ? '等待数据' : 'Waiting';
  const value = new Date(timestamp * 1000);
  return value.toLocaleTimeString(lang === 'zh' ? 'zh-CN' : 'en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}



export function statusText(status: RuntimeServiceStatus['status'], lang: Lang) {
  if (status === 'online') return lang === 'zh' ? '在线' : 'Online';
  if (status === 'offline') return lang === 'zh' ? '离线' : 'Offline';
  return lang === 'zh' ? '未知' : 'Unknown';
}



export function RuntimePanel({
  t,
  lang,
  navItems,
  activeAnchorId,
  onNavigate,
  onClose,
}: {
  t: ConfigText;
  lang: Lang;
  navItems: ConversationNavItem[];
  activeAnchorId: string;
  onNavigate: (anchorId: string) => void;
  onClose: () => void;
}) {
  const [runtimeStatus, setRuntimeStatus] = useState<RuntimeStatus | null>(null);
  const [runtimeError, setRuntimeError] = useState('');
  const [runtimeLoading, setRuntimeLoading] = useState(true);
  useEffect(() => {
    let cancelled = false;
    let inFlight = false;
    let activeController: AbortController | null = null;

    const fetchRuntimeStatus = async () => {
      if (inFlight) return;
      inFlight = true;
      const started = performance.now();
      const controller = new AbortController();
      activeController = controller;
      const timeout = window.setTimeout(() => controller.abort(), 2400);
      try {
        const response = await fetch(`${API_BASE}/runtime_status`, { cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const data = (await response.json()) as RuntimeStatus;
        if (cancelled) return;
        setRuntimeStatus({
          ...data,
          latency_ms: typeof data.latency_ms === 'number' ? data.latency_ms : Math.round(performance.now() - started),
        });
        setRuntimeError('');
      } catch (error) {
        if (cancelled) return;
        setRuntimeStatus(null);
        const isAbort = error instanceof DOMException && error.name === 'AbortError';
        setRuntimeError(isAbort ? (lang === 'zh' ? '状态接口超时' : 'Status timeout') : (lang === 'zh' ? '后端未连接' : 'Backend offline'));
      } finally {
        window.clearTimeout(timeout);
        if (!cancelled) setRuntimeLoading(false);
        inFlight = false;
        activeController = null;
      }
    };

    fetchRuntimeStatus();
    const interval = window.setInterval(fetchRuntimeStatus, 3000);
    return () => {
      cancelled = true;
      activeController?.abort();
      window.clearInterval(interval);
    };
  }, [lang]);

  const browserCores = typeof navigator !== 'undefined' ? navigator.hardwareConcurrency || 0 : 0;
  const cores = runtimeStatus?.system?.logical_cores || browserCores;
  const metrics = useMemo(() => {
    return {
      cpu: clampPercent(runtimeStatus?.system?.cpu_percent),
      ram: clampPercent(runtimeStatus?.system?.memory_percent),
      disk: clampPercent(runtimeStatus?.system?.disk_percent),
      latency: typeof runtimeStatus?.latency_ms === 'number' ? runtimeStatus.latency_ms : 0,
      skills: runtimeStatus?.skills?.total ?? 0,
    };
  }, [runtimeStatus]);
  const isOnline = runtimeStatus?.backend?.status === 'online' || runtimeStatus?.ok === true;
  const isDegraded = isOnline && runtimeStatus?.backend?.agent_status === 'degraded';
  const backendLabel = isDegraded ? (lang === 'zh' ? '降级' : 'Degraded') : isOnline ? (lang === 'zh' ? '在线' : 'Online') : runtimeLoading ? (lang === 'zh' ? '检测中' : 'Checking') : (lang === 'zh' ? '离线' : 'Offline');
  const updatedLabel = runtimeStatus ? `${lang === 'zh' ? '更新' : 'Updated'} ${formatUpdatedAt(runtimeStatus.timestamp, lang)}` : runtimeError || (lang === 'zh' ? '等待状态' : 'Waiting for status');
  const services = runtimeStatus?.services || [];

  const localDetails = useMemo(() => {
    const details: string[] = [];
    if (runtimeStatus?.backend?.agent_status === 'degraded') details.push(lang === 'zh' ? '智能体降级' : 'Agent degraded');
    if (runtimeStatus?.backend?.pid) details.push(`PID ${runtimeStatus.backend.pid}`);
    if (runtimeStatus?.process?.memory_mb) details.push(`${runtimeStatus.process.memory_mb.toFixed(1)} MB`);
    if (runtimeStatus?.process?.threads) details.push(lang === 'zh' ? `${runtimeStatus.process.threads} 线程` : `${runtimeStatus.process.threads} threads`);
    return details.join(' · ');
  }, [lang, runtimeStatus]);

  return (
    <div className="flex h-full w-[360px] flex-col border-l border-[var(--border)] bg-[var(--bg-panel)] shadow-2xl">
      <div className="flex h-16 items-center justify-between border-b border-[var(--border)] px-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="grid h-9 w-9 place-items-center rounded-lg bg-[var(--bg-soft)] text-[var(--accent)]">
            <Server className="h-5 w-5" />
          </div>
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold">{t.monitorTitle}</div>
            <div className="truncate text-xs text-[var(--text-muted)]">
              {cores ? (lang === 'zh' ? `${cores} 逻辑核心` : `${cores} logical cores`) : updatedLabel}
            </div>
          </div>
        </div>
        <button className="rounded-lg p-2 text-[var(--text-muted)] hover:bg-[var(--bg-soft)] hover:text-[var(--text-main)] xl:hidden" onClick={onClose} aria-label="Close monitor">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-4">
        <ConversationNavigator lang={lang} items={navItems} activeAnchorId={activeAnchorId} onNavigate={onNavigate} />

        <div className="rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center gap-2">
            <ShieldCheck className="h-4 w-4 text-[var(--mint-strong)]" />
            <div className="text-sm font-semibold">{t.monitorAirGap}</div>
          </div>
          <p className="text-sm leading-6 text-[var(--text-muted)]">{t.monitorAirDesc}</p>
          <div className="mt-4 flex items-center justify-between rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm">
            <span className="text-[var(--text-muted)]">{t.monitorLockLabel}</span>
            <span className={`flex items-center gap-2 font-semibold ${isDegraded ? 'text-[var(--amber)]' : isOnline ? 'text-[var(--mint-strong)]' : runtimeLoading ? 'text-[var(--amber)]' : 'text-[var(--danger)]'}`}>
              <span className={`h-2 w-2 rounded-full ${isDegraded ? 'bg-[var(--amber)]' : isOnline ? 'bg-[var(--ok)]' : runtimeLoading ? 'bg-[var(--amber)]' : 'bg-[var(--danger)]'}`} />
              {backendLabel}
            </span>
          </div>
          <div className="mt-2 text-xs text-[var(--text-muted)]">{updatedLabel}</div>
        </div>

        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-4 text-sm font-semibold">{t.monitorHardware}</div>
          <MetricBar icon={<Activity className="h-4 w-4" />} label={t.monitorCpu} value={metrics.cpu} accent="var(--accent)" detail={runtimeStatus ? `${metrics.cpu.toFixed(0)}%` : '--'} />
          <MetricBar icon={<Database className="h-4 w-4" />} label={t.monitorMem} value={metrics.ram} accent="var(--mint-strong)" detail={runtimeStatus ? `${metrics.ram.toFixed(0)}%` : '--'} />
          <MetricBar icon={<Globe2 className="h-4 w-4" />} label={t.monitorNet} value={metrics.disk} accent="var(--amber)" detail={runtimeStatus ? `${metrics.disk.toFixed(0)}%` : '--'} />
        </div>

        <div className="mt-4 grid grid-cols-2 gap-3">
          <SmallMetric label={t.monitorCpuLoad} value={runtimeStatus ? String(metrics.skills) : '--'} icon={<Code2 className="h-4 w-4" />} />
          <SmallMetric label={t.monitorBattery} value={runtimeStatus ? `${metrics.latency}ms` : '--'} icon={<Activity className="h-4 w-4" />} />
        </div>

        <div className="mt-4 rounded-lg border border-[var(--border)] bg-white p-4 shadow-sm">
          <div className="mb-3 flex items-center justify-between text-sm font-semibold">
            <span>{lang === 'zh' ? '服务探测' : 'Service Checks'}</span>
            <span className="font-mono text-xs text-[var(--text-muted)]">{services.length}</span>
          </div>
          {services.length === 0 ? (
            <div className="rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm text-[var(--text-muted)]">{runtimeError || (lang === 'zh' ? '暂无状态数据' : 'No status data')}</div>
          ) : (
            <div className="space-y-2">
              {services.map((service) => (
                <div key={service.name} className="flex items-center justify-between gap-3 rounded-lg bg-[var(--bg-soft)] px-3 py-2 text-sm">
                  <span className="flex min-w-0 items-center gap-2">
                    <span className={`h-2 w-2 shrink-0 rounded-full ${service.status === 'online' ? 'bg-[var(--ok)]' : service.status === 'offline' ? 'bg-[var(--danger)]' : 'bg-[var(--amber)]'}`} />
                    <span className="truncate font-medium text-[var(--text-main)]">{service.label || service.name}</span>
                  </span>
                  <span className="shrink-0 font-mono text-xs text-[var(--text-muted)]">
                    {statusText(service.status, lang)}
                    {service.port ? `:${service.port}` : ''}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="mt-4 rounded-lg border border-[var(--border)] bg-[var(--bg-soft)] p-4 text-sm text-[var(--text-muted)]">
          <div className="mb-1 font-semibold text-[var(--text-main)]">{t.monitorLocal}</div>
          <div>{localDetails || updatedLabel}</div>
          {runtimeStatus?.backend?.startup_error && (
            <div className="mt-2 overflow-hidden text-ellipsis whitespace-nowrap text-[var(--amber)]" title={runtimeStatus.backend.startup_error}>
              {runtimeStatus.backend.startup_error}
            </div>
          )}
          {runtimeStatus?.backend?.uptime_sec !== undefined && (
            <div className="mt-2">{lang === 'zh' ? '运行时长' : 'Uptime'} {formatUptime(runtimeStatus.backend.uptime_sec)}</div>
          )}
        </div>
      </div>
    </div>
  );
}



