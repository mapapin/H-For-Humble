import React, { useEffect, useState } from 'react';
import { ArrowDownToLine, ArrowLeft, ArrowRight, ArrowUpRight, CheckCircle2, Clock3, Eye, LockKeyhole, Pause, Play, ShieldCheck, Square, Target, TriangleAlert, X } from 'lucide-react';
import { api, date, Dialog, ErrorNote, host, PERSONAS, pname, PersonaIcon, Spinner, Status, terminal, total } from './shared';
import type { Audit, Run } from './shared';

type Case = { persona: string; task: string; run: number };
const sameCase = (a: Partial<Case>, b: Partial<Case>) => a.persona === b.persona && a.task === b.task && a.run === b.run;
const technical = (r: Run) => r.status === 'harness_error' || r.status === 'failed' || r.status === 'timed_out';
const average = (values: (number | undefined)[]) => { const valid = values.filter((n): n is number => typeof n === 'number'); return valid.length ? valid.reduce((sum, n) => sum + n, 0) / valid.length : null; };
const duration = (seconds: number | null) => seconds == null ? '—' : seconds >= 60 ? `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s` : `${Math.round(seconds)}s`;
const number = (n: number | null) => n == null ? '—' : n.toFixed(1).replace(/\.0$/, '');
const caseState = (audit: Audit, selected: Case) => {
  const record = audit.results?.find(r => sameCase(r, selected));
  if (record) return technical(record) ? 'Technical error' : record.success ? 'Passed' : 'Failed';
  if (!terminal(audit.status) && sameCase(audit.progress, selected)) return audit.status === 'stopping' ? 'Stopping' : 'Running';
  return terminal(audit.status) ? 'Not run' : 'Queued';
};

export function AuditDetail({ id, onBack, onRerun, onUpdate }: { id: string; onBack: () => void; onRerun: (a: Audit) => void; onUpdate: () => Promise<void> }) {
  const [audit, setAudit] = useState<Audit | null>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [tab, setTab] = useState('overview'), [connected, setConnected] = useState(true);
  const [selected, setSelected] = useState<Case | null>(null), [stopConfirm, setStopConfirm] = useState(false);
  const [showAllBarriers, setShowAllBarriers] = useState(false);
  const closeCase = React.useCallback(() => setSelected(null), []);
  const closeConfirm = React.useCallback(() => setStopConfirm(false), []);
  useEffect(() => {
    setAudit(null); setError(''); setTab('overview'); setSelected(null); setConnected(true); setShowAllBarriers(false);
    let source: EventSource | undefined, disposed = false;
    api<Audit>(`/audits/${id}`).then(a => {
      if (disposed) return;
      setAudit(a);
      if (!terminal(a.status)) {
        source = new EventSource(`/api/audits/${id}/events`);
        source.onmessage = e => { const next = JSON.parse(e.data) as Audit; setAudit(next); setConnected(true); if (terminal(next.status)) { source?.close(); void onUpdate(); } };
        source.onerror = () => setConnected(false);
        source.onopen = () => setConnected(true);
      }
    }).catch(e => { if (!disposed) setError(e.message); });
    return () => { disposed = true; source?.close(); };
  }, [id, onUpdate]);
  async function control(action: string) {
    setBusy(true); setError('');
    try { await api(`/audits/${id}/${action}`, { method: 'POST' }); setAudit(await api<Audit>(`/audits/${id}`)); await onUpdate(); setStopConfirm(false); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  if (!audit) return <>{error ? <ErrorNote message={error} /> : <div className="loading-panel"><Spinner />Opening this perspective…</div>}<button className="back-button" onClick={onBack}><ArrowLeft size={15} />Back to history</button></>;
  const records = audit.results || [], valid = records.filter(r => !technical(r));
  const passed = valid.filter(r => r.success).length, failed = valid.length - passed;
  const errors = records.length - valid.length, count = total(audit), done = records.length;
  const isActive = !terminal(audit.status);
  const obstacles = records.flatMap(r => (r.report?.obstacles || []).map(o => ({ ...o, record: r })));
  const steps = average(valid.map(r => r.steps)), agentTime = average(valid.map(r => r.agent_duration_s));
  const activeCase = audit.progress.persona && audit.progress.task && audit.progress.run != null
    ? { persona: audit.progress.persona, task: audit.progress.task, run: audit.progress.run } : null;
  function openPersona(persona: string, task?: string) {
    if (activeCase && persona === activeCase.persona && (!task || task === activeCase.task) && !terminal(audit!.status)) { setSelected(activeCase); return; }
    const first = records.find(r => r.persona === persona && (!task || r.task === task));
    setSelected({ persona, task: task || first?.task || audit!.config.tasks[0].id!, run: first?.run || 0 });
  }
  return <>
    <button className="back-button" onClick={onBack}><ArrowLeft size={15} />Audit history</button>
    <div className="page-heading audit-heading"><div><div className="eyebrow">{host(audit.url)} <span className="eyebrow-divider">/</span> {date(audit.created)}</div><h1>{audit.name}</h1><p>{audit.config.tasks.length} journeys · {audit.config.personas.length} perspectives · {count} planned runs{audit.config.vault_name && <span className="audit-vault-link"><LockKeyhole size={12} />{audit.config.vault_name}</span>}</p></div><div className="heading-actions"><Status value={audit.status} />{isActive ? <><button className="button secondary" disabled={busy || audit.status === 'stopping'} onClick={() => void control(audit.status === 'paused' ? 'resume' : 'pause')}>{audit.status === 'paused' ? <Play size={14} /> : <Pause size={14} />}{audit.status === 'paused' ? 'Resume queue' : 'Pause queue'}</button><button className="button stop-button" disabled={busy || audit.status === 'stopping'} onClick={() => setStopConfirm(true)}><Square size={12} />Stop</button></> : <button className="button secondary" onClick={() => onRerun(audit)}><Play size={14} />Run again</button>}</div></div>
    {error && <ErrorNote message={error} />}{audit.error && <ErrorNote message={audit.error} />}
    {!connected && isActive && <div className="soft-note"><Clock3 size={16} />Reconnecting to the live feed. Your audit continues on the server.</div>}

    <section className="perspective-overview" aria-label="Test statistics and perspectives">
      <div className="results-head"><div><h2>Every perspective tells a story.</h2><p>Choose a perspective to inspect each journey and its browser preview.</p></div><span className="tiny-tag">{done} / {count} RUNS OBSERVED</span></div>
      <div className="audit-stat-strip">
        <Metric label="Tests passed" value={String(passed)} detail={`of ${valid.length} evaluated`} tone="positive" />
        <Metric label="Tests failed" value={String(failed)} detail={`${errors} technical error${errors !== 1 ? 's' : ''} excluded`} tone="negative" />
        <Metric label="Barriers reported" value={String(obstacles.length)} detail={`${obstacles.filter(o => o.severity === 'blocking').length} blocking observations`} />
        <Metric label="Average steps" value={number(steps)} detail="per evaluated test" />
      </div>
      <div className="result-personas">{audit.config.personas.map(persona => {
        const runs = records.filter(r => r.persona === persona), measured = runs.filter(r => !technical(r));
        const successes = measured.filter(r => r.success).length;
        const barriers = runs.reduce((n, r) => n + (r.report?.obstacles.length || 0), 0);
        const planned = audit.config.tasks.length * audit.config.repetitions;
        const waiting = planned - runs.length;
        return <button key={persona} className={`result-persona perspective-button ${persona === 'baseline' ? 'reference' : ''}`} onClick={() => openPersona(persona)} aria-label={`Inspect ${pname(persona)} test cases`}>
          <div className="result-persona-top"><PersonaIcon id={persona} />{persona === 'baseline' ? <span className="tiny-tag">REFERENCE</span> : isActive && activeCase?.persona === persona ? <span className="tiny-tag live-case-tag"><span className="live-dot" />LIVE</span> : <ArrowUpRight size={14} />}</div>
          <h3>{pname(persona)}</h3><div className="perspective-counts"><strong>{measured.length ? successes : '—'}</strong><span>/ {measured.length} tested</span></div><span className="result-rate-caption">tests passed</span>
          <div className="persona-outcomes"><span>{measured.length - successes} failed</span><span>{barriers} barriers</span></div>

          <div className="perspective-card-foot"><span>{waiting > 0 ? `${waiting} ${isActive ? 'remaining' : 'not run'}` : runs.length - measured.length > 0 ? `${runs.length - measured.length} errors` : ''}</span><span>View cases<ArrowRight size={12} /></span></div>
        </button>;
      })}</div>
    </section>
    {isActive && <div className="active-case-banner"><div><span className="live-dot" /><span><strong>{audit.status === 'paused' ? 'Queue paused · current run continues' : audit.status === 'stopping' ? 'Stop requested' : activeCase ? `${pname(activeCase.persona)} is exploring` : 'Preparing the first test'}</strong><small>{audit.config.tasks.find(t => t.id === activeCase?.task)?.title || 'Connecting the dedicated browser'}{activeCase ? ` · Run ${activeCase.run + 1}` : ''} · {audit.progress.actions || 0} actions observed</small></span></div>{activeCase && <button className="button secondary" onClick={() => setSelected(activeCase)}><Eye size={15} />Open live case</button>}</div>}

    <div className="detail-tabs"><div className="detail-tab-buttons" role="tablist" aria-label="Audit views" onKeyDown={e => { const tabs = ['overview', 'evidence', 'activity']; const index = tabs.indexOf(tab); const next = e.key === 'ArrowRight' ? tabs[(index + 1) % 3] : e.key === 'ArrowLeft' ? tabs[(index + 2) % 3] : e.key === 'Home' ? tabs[0] : e.key === 'End' ? tabs[2] : null; if (next) { e.preventDefault(); setTab(next); document.getElementById(`audit-tab-${next}`)?.focus(); } }}>{['overview', 'evidence', 'activity'].map(t => <button key={t} id={`audit-tab-${t}`} role="tab" aria-controls="audit-tabpanel" tabIndex={tab === t ? 0 : -1} aria-selected={tab === t} onClick={() => setTab(t)}>{t.charAt(0).toUpperCase() + t.slice(1)}{t === 'evidence' && <span className="number-pill">{records.length}</span>}</button>)}</div><a className="text-button muted" href={`/api/audits/${id}/export`} download><ArrowDownToLine size={15} />Export JSON</a></div>
    <div id="audit-tabpanel" role="tabpanel" aria-labelledby={`audit-tab-${tab}`} tabIndex={0}>
      {tab === 'overview' && <>
        <section className="section journey-section"><div className="section-heading"><div><h2>Journey breakdown</h2><p>Measured outcomes, effort and timing for every journey and perspective.</p></div></div>{audit.summary.length ? <div className="table-wrap"><table className="breakdown-table"><thead><tr><th>Journey / Perspective</th><th>Passed / Tested</th><th>Avg. steps</th><th>Avg. agent time</th><th><span className="sr-only">View test cases</span></th></tr></thead><tbody>{audit.summary.map(row => {
          const tests = records.filter(r => r.task === row.task && r.persona === row.persona && !technical(r));
          return <tr key={row.task + row.persona}><td><strong>{audit.config.tasks.find(t => t.id === row.task)?.title || row.task}</strong><span>{pname(row.persona)}</span></td><td><b>{tests.filter(r => r.success).length}</b> / {tests.length}</td><td>{number(average(tests.map(r => r.steps)))}</td><td>{duration(average(tests.map(r => r.agent_duration_s)))}</td><td><button className="text-button case-link" onClick={() => openPersona(row.persona, row.task)}>View cases<ArrowUpRight size={13} /></button></td></tr>;
        })}</tbody></table></div> : <div className="empty-state"><span className="empty-icon"><Target size={23} /></span><h3>The first perspective is still taking shape.</h3><p>Results appear here as each run finishes. You can already select a perspective to see its planned cases.</p></div>}</section>
        <section className="section"><div className="section-heading"><div><h2>Barriers encountered <span className="number-pill">{obstacles.length}</span></h2><p>Open the exact test behind an observation.</p></div></div>{obstacles.length ? <div className="obstacle-list">{(showAllBarriers ? obstacles : obstacles.slice(0, 3)).map((o, i) => <div className="obstacle" key={i}><span className={`severity ${o.severity}`}>{o.severity}</span><div><h3>{o.what}</h3><p>{pname(o.record.persona)} · {audit.config.tasks.find(t => t.id === o.record.task)?.title} · Run {o.record.run + 1}<br />{o.where}</p></div><button className="icon-button" aria-label={`View ${pname(o.record.persona)} barrier evidence`} onClick={() => setSelected(o.record)}><ArrowUpRight size={17} /></button></div>)}</div> : <div className="quiet-note">{done ? 'No barriers reported in the observed runs.' : 'Observations will appear when a run finishes.'}</div>}{obstacles.length > 3 && <button className="button secondary show-more" onClick={() => setShowAllBarriers(!showAllBarriers)}>{showAllBarriers ? 'Show fewer barriers' : `Show all ${obstacles.length} barriers`}</button>}</section>
        <p className="report-footnote">Simulated journeys, not an accessibility certification. Technical errors are excluded from test outcomes.</p>
      </>}
      {tab === 'evidence' && <div className="case-evidence-list">{records.length ? records.map(r => <button className="case-evidence-row" key={r.tag} onClick={() => setSelected(r)}><PersonaIcon id={r.persona} /><span><strong>{pname(r.persona)}</strong><small>{audit.config.tasks.find(t => t.id === r.task)?.title} · Run {r.run + 1}</small></span><span className={`case-outcome ${technical(r) ? 'error' : r.success ? 'passed' : 'failed'}`}>{caseState(audit, r)}</span><span className="case-row-metrics">{r.steps ?? '—'} steps · {duration(r.agent_duration_s ?? null)}</span><Eye size={17} /></button>) : <div className="quiet-note">Screenshots and structured reports appear after each test. Select a perspective above to inspect a queued or live case.</div>}</div>}
      {tab === 'activity' && <section className="activity-panel"><h2>The exploration, step by step.</h2><div className="event-list">{audit.events?.length ? audit.events.map(e => <div className="event-item" key={e.id}><span className={`event-dot ${e.kind}`} /><time>{new Date(e.created * 1000).toLocaleTimeString('en-GB')}</time><p>{e.message}</p></div>) : <p>Preparing the event feed…</p>}</div></section>}
    </div>
    {selected && <CasePreview audit={audit} selected={selected} select={setSelected} close={closeCase} />}
    {stopConfirm && <Dialog close={closeConfirm} title="Stop this audit?"><div className="confirm-dialog"><span className="empty-icon"><Square size={22} /></span><h2>Stop this exploration?</h2><p>We’ll request cancellation of the active H session and skip the remaining runs. Results collected so far will stay in your history.</p><div className="confirm-actions"><button className="button secondary" onClick={closeConfirm}>Keep exploring</button><button className="button danger" disabled={busy} onClick={() => void control('stop')}>{busy ? <Spinner /> : <Square size={13} />}Stop audit</button></div></div></Dialog>}
  </>;
}

function Metric({ label, value, detail, tone = '' }: { label: string; value: string; detail: string; tone?: string }) {
  return <div className={`test-metric ${tone}`}><span>{label}</span><strong>{value}</strong><small>{detail}</small></div>;
}

function CasePreview({ audit, selected, select, close }: { audit: Audit; selected: Case; select: (c: Case) => void; close: () => void }) {
  const record = audit.results?.find(r => sameCase(r, selected));
  const live = !terminal(audit.status) && sameCase(audit.progress, selected);
  const state = caseState(audit, selected);
  const task = audit.config.tasks.find(t => t.id === selected.task);
  const screenshot = record?.final_screenshot ? `/api/audits/${audit.id}/files/${record.final_screenshot}`
    : live && audit.progress.preview_at ? `/api/audits/${audit.id}/files/live.png?t=${audit.progress.preview_at}` : null;
  const viewUrl = record?.agent_view_url || (live ? audit.progress.agent_view_url : undefined);
  return <Dialog close={close} title={`${pname(selected.persona)} test cases`} wide>
    <div className="modal-top"><div><span className="eyebrow">ONE CASE. ONE PERSPECTIVE.</span><h2>{pname(selected.persona)}</h2></div><button className="icon-button" aria-label="Close case preview" onClick={close}><X size={20} /></button></div>
    <div className="case-explorer"><aside className="case-selector"><label>Perspective<select value={selected.persona} onChange={e => select({ ...selected, persona: e.target.value })}>{audit.config.personas.map(p => <option value={p} key={p}>{pname(p)}</option>)}</select></label><span className="eyebrow">JOURNEYS & RUNS</span>
      {audit.config.tasks.map(t => <div className="case-task-group" key={t.id}><h3>{t.title}</h3>{Array.from({ length: audit.config.repetitions }, (_, run) => {
        const current = { persona: selected.persona, task: t.id!, run }, status = caseState(audit, current);
        return <button key={run} className={`case-select-button ${sameCase(current, selected) ? 'selected' : ''}`} aria-pressed={sameCase(current, selected)} onClick={() => select(current)}><span>Run {run + 1}</span><span className={`case-outcome ${status.toLowerCase().replaceAll(' ', '-')}`}>{status}</span></button>;
      })}</div>)}
    </aside><section className="case-detail"><div className="case-detail-heading"><div><h3>{task?.title}</h3><p>Run {selected.run + 1} · {pname(selected.persona)}</p></div><span className={`case-outcome ${state.toLowerCase().replaceAll(' ', '-')}`}>{live && <span className="live-dot" />}{state}</span></div>
      <div className="case-preview-image">{screenshot ? <a href={screenshot} target="_blank" rel="noreferrer"><img src={screenshot} alt={`${task?.title}, ${pname(selected.persona)}, run ${selected.run + 1}`} /><span>{live ? 'Live browser · updates automatically' : 'Final browser screenshot'}<ArrowUpRight size={13} /></span></a> : <div className="case-no-preview"><Eye size={29} strokeWidth={1.3} /><strong>{live ? 'Waiting for this case’s first screenshot' : record ? 'No screenshot was captured for this case' : state === 'Queued' ? 'This case is waiting its turn' : 'This case was not run'}</strong><p>Previews belong to the selected journey, perspective and run.</p></div>}</div>
      <div className="case-stats"><Metric label="Steps" value={record?.steps != null ? String(record.steps) : '—'} detail="agent navigation steps" /><Metric label="Agent time" value={duration(record?.agent_duration_s ?? null)} detail="excludes browser setup" /><Metric label="Barriers" value={record ? String(record.report?.obstacles.length || 0) : '—'} detail="reported in this case" />{live && <Metric label="Actions" value={String(audit.progress.actions || 0)} detail="observed so far" />}</div>
      {record?.report && <div className="case-report"><span className="eyebrow">OBSERVATION</span><p>{record.report.summary}</p>{record.report.answer && <div className="run-answer"><span className="eyebrow">REPORTED ANSWER</span><p>{record.report.answer}</p></div>}{record.report.obstacles.map((o, i) => <div key={i} className="case-barrier"><span className={`severity ${o.severity}`}>{o.severity}</span><p>{o.what}<small>{o.where}</small></p></div>)}</div>}
      {record?.error && <ErrorNote message={record.error} />}
      <details className="case-goal"><summary>Journey goal</summary><p>{task?.goal}</p></details>
      {viewUrl && <a className="button secondary" href={viewUrl} target="_blank" rel="noreferrer">{live ? 'Watch' : 'Replay'} in H Agent View<ArrowUpRight size={15} /></a>}
    </section></div>
  </Dialog>;
}
