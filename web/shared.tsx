import React, { useEffect } from 'react';
import { MousePointer2, Eye, Layers3, Fingerprint, Keyboard, TriangleAlert, Loader2 } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

export type User = { id: string; name: string; email: string };
export type Journey = { id?: string; title: string; goal: string; expect_answer_contains?: string | null };
export type Summary = { task: string; persona: string; runs: number; success_rate: number | null; gap_vs_baseline?: number; mean_steps: number | null; effort_vs_baseline?: number; mean_agent_time_s: number | null; harness_errors: number };
export type Obstacle = { where: string; what: string; severity: string };
export type ReplayStep = { t?: number; thought?: string; frame?: number; url?: string; action?: { tool: string; kind: string; label: string; x?: number; y?: number; keys?: string[]; text?: string }; effects?: { jitter?: [number, number]; blocked?: boolean; error?: boolean } };
export type Replay = { frames: { file?: string; w?: number; h?: number }[]; steps: ReplayStep[] };
export type RunMetrics = { steps?: number; time_s?: number; clicks: number; keys: number; typing: number; scrolls: number; blocked: number; errors: number; retries: number; barriers: Record<string, number>; cost_usd?: number };
export type Run = { duration_s?: number; actions?: { cmd: string; blocked?: boolean }[]; tag: string; persona: string; task: string; run: number; status: string; success: boolean; steps?: number; agent_duration_s?: number; final_screenshot?: string; agent_view_url?: string; report?: { summary: string; answer: string; completed: boolean; obstacles: Obstacle[] }; error?: string; replay?: Replay; metrics?: RunMetrics; lane?: number; round?: number; patched?: boolean; retry_of?: string; session_id?: string; session_closed?: boolean; stalled?: boolean; stuck_session_ids?: string[]; assisted?: boolean };
export type PerspectiveScore = { score: number | null; grade: string | null; runs: number; tested: number; errors: number; completed: number; completion: number | null; steps: number | null; time_s: number | null; steps_vs_baseline: number | null; time_vs_baseline: number | null; clicks: number; keys: number; blocked: number; retries: number; barriers: number; blocking: number; penalties: Record<string, number> };
export type Scores = { score?: number | null; grade?: string | null; perspectives?: Record<string, PerspectiveScore>; can_complete?: number; tested_perspectives?: number; impaired_blocked?: number; baseline_ok?: boolean };
export type Context = { known_issues?: string[]; declared_rate?: string; organisation?: string; sector?: string; accessibility_statement_url?: string; declared_compliance?: string; audit_date?: string; self_assessed?: boolean; legal_context?: string; sources?: { name?: string; url: string }[] };
export type StaticViolation = { id: string; impact?: string; help: string; helpUrl?: string; nodes: number; personas: string[]; blocked_someone: boolean; related_to_barrier: boolean; wcag: string[]; pages: string[] };
export type StaticScan = { engine?: string; pages?: { url: string; violations: number }[]; violations?: StaticViolation[]; totals?: { rules: number; nodes: number; related: number; blocking: number }; error?: string };
export type Lane = { persona?: string; task?: string; run?: number; round?: number; state?: string; started_at?: number; finished_at?: number; session_id?: string; agent_view_url?: string; message?: string; actions?: number; preview_at?: number; step?: number; thought?: string; action?: string; tag?: string; success?: boolean; error?: boolean; steps?: number };
export type Audit = { id: string; project_id: string; name: string; url: string; status: string; created: number; finished?: number; error?: string; config: { tasks: Journey[]; personas: string[]; repetitions: number; vault_id?: string | null; vault_name?: string; mode?: 'standard' | 'auto'; max_rounds?: number; uses_login?: boolean; uses_headers?: boolean; patch?: Fixes['patch'] }; fixes?: Fixes; rounds?: Round[]; parent_id?: string | null; parent?: { id: string; name: string; summary: Summary[]; fixes?: Fixes; scores?: Scores } | null; reevaluations?: { id: string; name: string; status: string; created: number; summary: Summary[] }[]; summary: Summary[]; results?: Run[]; progress: { completed?: number; persona?: string; task?: string; run?: number; actions?: number; agent_view_url?: string; last_action?: string; preview_at?: number; lanes?: Record<string, Lane>; lanes_total?: number }; scores?: Scores; context?: Context; static_scan?: StaticScan; events?: { id: number; created: number; kind: string; message: string }[] };
// Fix workflows: a patch of fixes (preview CSS/JS + the real code change), before/after images, auto-mode rounds.
export type Fix = { id: string; title: string; wcag: string; personas: string[]; page_url: string; description: string; css: string; js: string; code_change: string; source?: string; scope?: string };
export type Comparison = { kind: 'fix' | 'persona'; id: string; title: string; persona: string; page_url: string; before?: string; after?: string; split?: number; wcag?: string | null; error?: string };
export type Fixes = { source?: string; devin_session_url?: string | null; patch: { summary?: string; fixes: Fix[] }; comparisons: Comparison[]; new_fix_ids?: string[] };
export type Round = { round: number; score: number; completion_score?: number; scores?: Record<string, number | null>; rates: Record<string, number | null>; fixes_applied: string[]; passed: boolean; new_fixes?: string[]; stopped?: string; summary: Summary[] };
export type Project = { id: string; name: string; url: string; audit_count: number; last_audit: number };
export type Setup = { h_configured: boolean; chrome_ready: boolean; linkup_configured?: boolean; devin_configured?: boolean };
export type Quota = { limit: number; active: number; available: number };

export const PERSONAS: { id: string; name: string; short: string; description: string; icon: LucideIcon; color: string }[] = [
  { id: 'baseline', name: 'Baseline', short: 'Reference experience', description: 'Standard vision, mouse and keyboard. The reference for every comparison.', icon: MousePointer2, color: 'indigo' },
  { id: 'low_vision', name: 'Low vision', short: '200% zoom + blur', description: 'Magnified layout and blurred vision reveal reading and reflow barriers.', icon: Eye, color: 'rose' },
  { id: 'color_blind', name: 'Colour blindness', short: 'Deuteranopia', description: 'Red–green colour blindness tests whether meaning depends on colour.', icon: Layers3, color: 'amber' },
  { id: 'tremor', name: 'Hand tremor', short: 'Imprecise pointer', description: 'Pointer movement varies by ±10 pixels to test small and crowded targets.', icon: Fingerprint, color: 'mint' },
  { id: 'keyboard_only', name: 'Keyboard only', short: 'No mouse input', description: 'Tab, Enter and arrow keys only. Mouse actions are blocked by the browser bridge.', icon: Keyboard, color: 'blue' },
];
export const terminal = (status: string) => ['completed', 'failed', 'stopped'].includes(status);
export const pname = (id: string) => PERSONAS.find(p => p.id === id)?.name || id;
export const host = (url: string) => { try { return new URL(url).host; } catch { return url; } };
export const date = (time: number) => new Date(time * 1000).toLocaleDateString('en-GB', { day: 'numeric', month: 'short' });
export const pct = (n: number | null | undefined) => n == null ? '—' : `${Math.round(n * 100)}%`;
export const total = (a: Audit) => a.config.tasks.length * a.config.personas.length * a.config.repetitions;
export const mean = (rows: Summary[]) => { const valid = rows.filter(r => r.success_rate != null); return valid.length ? valid.reduce((n, r) => n + r.success_rate!, 0) / valid.length : null; };

export async function api<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(`/api${path}`, { ...options, headers: { 'Content-Type': 'application/json', ...options?.headers } });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail;
    throw new Error(typeof detail === 'string' ? detail : Array.isArray(detail) ? detail.map((e: { msg: string }) => e.msg).join('. ') : 'Something went wrong. Please try again.');
  }
  return data;
}

export function Mark({ small = false }: { small?: boolean }) {
  return <span className={`brand ${small ? 'small' : ''}`}><span className="brand-mark"><span /><span /><i /></span>humble<span className="brand-period">.</span></span>;
}
export function Status({ value }: { value: string }) { return <span className={`status ${value}`}><span />{({ queued: 'Preparing', running: 'Running', paused: 'Queue paused', stopping: 'Stopping', stopped: 'Stopped', failed: 'Technical error', completed: 'Completed' } as Record<string, string>)[value] || value}</span>; }
export function ErrorNote({ message }: { message: string }) { return <div className="error-note" role="alert"><TriangleAlert size={17} /><span>{message}</span></div>; }
export function Spinner() { return <Loader2 className="spin" size={18} />; }
export function PersonaIcon({ id, size = 20 }: { id: string; size?: number }) { const p = PERSONAS.find(p => p.id === id) || PERSONAS[0]; const Icon = p.icon; return <span className={`persona-icon ${p.color}`}><Icon size={size} strokeWidth={1.6} /></span>; }
export function Dialog({ children, close, title, wide = false }: { children: React.ReactNode; close: () => void; title: string; wide?: boolean }) {
  const ref = React.useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement;
    const previous = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    ref.current?.focus();
    const key = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close();
      if (e.key === 'Tab') {
        const nodes = ref.current?.querySelectorAll<HTMLElement>('button:not(:disabled),input,textarea,select,a[href],[tabindex="0"]');
        if (!nodes?.length) return;
        const first = nodes[0], last = nodes[nodes.length - 1];
        if (e.shiftKey && (document.activeElement === first || document.activeElement === ref.current)) { e.preventDefault(); last.focus(); }
        else if (!e.shiftKey && (document.activeElement === last || document.activeElement === ref.current)) { e.preventDefault(); first.focus(); }
      }
    };
    document.addEventListener('keydown', key);
    return () => { document.body.style.overflow = previous; document.removeEventListener('keydown', key); before?.focus(); };
  }, [close]);
  return <div className="modal-backdrop" onMouseDown={e => { if (e.target === e.currentTarget) close(); }}><div className={`modal ${wide ? 'wide-modal' : ''}`} role="dialog" aria-modal="true" aria-label={title} tabIndex={-1} ref={ref}>{children}</div></div>;
}


export type VaultEntry = { id: string; name: string; origin: string; has_login: boolean; header_count: number; created: number; updated: number };

export const duration = (seconds: number | null | undefined) => seconds == null ? '—' : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s` : `${Math.round(seconds)}s`;
export const technical = (r: Run) => r.status === 'harness_error' || r.status === 'failed' || r.status === 'timed_out';
export const gradeTone = (grade?: string | null) => !grade ? 'none' : grade === 'A' || grade === 'B' ? 'good' : grade === 'C' ? 'fair' : 'poor';

/** Score ring: the arc fills with the score, coloured by grade. */
export function ScoreRing({ score, grade, size = 120, stroke = 10, label }: { score: number | null | undefined; grade?: string | null; size?: number; stroke?: number; label?: string }) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, value = score == null ? 0 : Math.max(0, Math.min(100, score));
  return <span className={`score-ring tone-${gradeTone(grade)}`} style={{ width: size, height: size }} role="img" aria-label={score == null ? 'Not scored yet' : `${label || 'Score'} ${score} out of 100, grade ${grade}`}>
    <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden="true"><circle cx={size / 2} cy={size / 2} r={r} className="ring-track" strokeWidth={stroke} fill="none" /><circle cx={size / 2} cy={size / 2} r={r} className="ring-value" strokeWidth={stroke} fill="none" strokeLinecap="round" strokeDasharray={`${c * value / 100} ${c}`} transform={`rotate(-90 ${size / 2} ${size / 2})`} /></svg>
    <span className="ring-label" aria-hidden="true"><strong style={{ fontSize: size * 0.3 }}>{score ?? '—'}</strong>{grade && size >= 64 && <small>Grade {grade}</small>}</span>
  </span>;
}
