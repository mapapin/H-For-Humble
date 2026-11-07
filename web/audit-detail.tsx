import React, { useEffect, useState } from 'react';
import { ArrowDownToLine, ArrowLeft, ArrowUpRight, Clock3, LockKeyhole, Pause, Play, RotateCcw, Sparkles, Square } from 'lucide-react';
import { api, date, Dialog, ErrorNote, host, pname, Spinner, Status, terminal, total } from './shared';
import type { Audit, Run } from './shared';
import { FixesPanel } from './fixes';
import { BarrierList, ContextCard, EffortChart, PerspectiveGrid, ScoreHero, StaticCard } from './report';
import { ReplayDialog } from './replay';

const TABS = ['report', 'fixes', 'activity'] as const;
type Tab = typeof TABS[number];

export function AuditDetail({ id, onBack, onRerun, onUpdate, onOpen = next => { location.hash = `audit/${next}`; } }: { id: string; onBack: () => void; onRerun: (a: Audit) => void; onUpdate: () => Promise<void>; onOpen?: (id: string) => void }) {
  const [audit, setAudit] = useState<Audit | null>(null), [error, setError] = useState('');
  const [busy, setBusy] = useState(false), [tab, setTab] = useState<Tab>('report'), [connected, setConnected] = useState(true);
  const [replay, setReplay] = useState<Run | null>(null), [stopConfirm, setStopConfirm] = useState(false);
  const closeReplay = React.useCallback(() => setReplay(null), []);
  const closeConfirm = React.useCallback(() => setStopConfirm(false), []);
  useEffect(() => {
    setAudit(null); setError(''); setTab('report'); setReplay(null); setConnected(true);
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
  if (!audit) return <>{error ? <ErrorNote message={error} /> : <div className="loading-panel"><Spinner />Opening the report…</div>}<button className="back-button" onClick={onBack}><ArrowLeft size={15} />All audits</button></>;
  const records = audit.results || [];
  const isActive = !terminal(audit.status);
  const fixCount = audit.fixes?.patch?.fixes?.length || audit.parent?.fixes?.patch?.fixes?.length || 0;
  const lanes = Object.values(audit.progress?.lanes || {}).filter(l => l.state === 'running' || l.state === 'starting').length;
  const waitingLanes = Object.values(audit.progress?.lanes || {}).filter(l => l.state === 'waiting').length;
  const replayable = records.filter(r => r.replay?.steps?.length || r.final_screenshot);
  const roundLabel = audit.rounds?.length ? ` · round ${audit.rounds.length + (isActive ? 1 : 0)}` : '';
  return <>
    <button className="back-button" onClick={onBack}><ArrowLeft size={15} />All audits</button>
    <div className="report-head">
      <div>
        <div className="eyebrow">{host(audit.url)} <span className="eyebrow-divider">/</span> {date(audit.created)}{audit.parent && <><span className="eyebrow-divider">/</span><button className="text-button" onClick={() => onOpen(audit.parent!.id)}>re-evaluation of “{audit.parent.name}”</button></>}</div>
        <h1>{audit.name}</h1>
        <p>{audit.config.tasks.map(t => t.title).join(' · ')} · {audit.config.personas.length} perspectives{audit.config.repetitions > 1 ? ` × ${audit.config.repetitions} runs` : ''}{audit.config.mode === 'auto' ? ' · fix until it passes' : ''}{audit.config.patch ? ' · with suggested fixes injected' : ''}{audit.config.vault_name && <span className="audit-vault-link"><LockKeyhole size={12} />{audit.config.vault_name}</span>}</p>
      </div>
      <div className="heading-actions"><Status value={audit.status} />{isActive ? <>
        <button className="button secondary" disabled={busy || audit.status === 'stopping'} onClick={() => void control(audit.status === 'paused' ? 'resume' : 'pause')}>{audit.status === 'paused' ? <Play size={14} /> : <Pause size={14} />}{audit.status === 'paused' ? 'Resume' : 'Pause'}</button>
        <button className="button stop-button" disabled={busy || audit.status === 'stopping'} onClick={() => setStopConfirm(true)}><Square size={12} />Stop</button>
      </> : <button className="button secondary" onClick={() => onRerun(audit)}><RotateCcw size={14} />Run again</button>}</div>
    </div>
    {error && <ErrorNote message={error} />}{audit.error && <ErrorNote message={audit.error} />}
    {!connected && isActive && <div className="soft-note"><Clock3 size={16} />Reconnecting to the live feed. Your audit continues on the server.</div>}
    {isActive && <div className="live-strip"><span className="live-dot" /><strong>{audit.status === 'paused' ? 'Paused: running agents finish, no new run starts' : audit.status === 'stopping' ? 'Stopping: asking H to end every session' : lanes ? `${lanes} agent${lanes > 1 ? 's' : ''} exploring in parallel${waitingLanes ? `, ${waitingLanes} waiting for a free H slot` : ''}${roundLabel}` : waitingLanes ? `Waiting for free H slots${roundLabel}` : `Preparing browsers${roundLabel}`}</strong><span>{audit.progress.completed || 0} of {total(audit)} runs finished · each agent drives its own Chrome with its impairment applied</span></div>}

    <ScoreHero audit={audit} />
    <ContextCard audit={audit} />

    <div className="detail-tabs"><div className="detail-tab-buttons" role="tablist" aria-label="Report views" onKeyDown={e => { const index = TABS.indexOf(tab); const next = e.key === 'ArrowRight' ? TABS[(index + 1) % TABS.length] : e.key === 'ArrowLeft' ? TABS[(index + TABS.length - 1) % TABS.length] : e.key === 'Home' ? TABS[0] : e.key === 'End' ? TABS[TABS.length - 1] : null; if (next) { e.preventDefault(); setTab(next); document.getElementById(`audit-tab-${next}`)?.focus(); } }}>
      {TABS.map(t => <button key={t} id={`audit-tab-${t}`} role="tab" aria-controls="audit-tabpanel" tabIndex={tab === t ? 0 : -1} aria-selected={tab === t} onClick={() => setTab(t)}>{t === 'report' ? 'Perspectives' : t === 'fixes' ? <><Sparkles size={14} />Fixes</> : 'Activity'}{t === 'fixes' && fixCount ? <span className="number-pill">{fixCount}</span> : null}</button>)}
    </div><a className="text-button muted" href={`/api/audits/${id}/export`} download><ArrowDownToLine size={15} />Export JSON</a></div>
    <div id="audit-tabpanel" role="tabpanel" aria-labelledby={`audit-tab-${tab}`}>
      {tab === 'report' && <>
        <PerspectiveGrid audit={audit} onReplay={setReplay} />
        <div className="report-columns">
          <section className="section"><div className="section-heading"><div><h2>What stopped users</h2><p>Reported by the agents, worst first. Open the replay to see it happen.</p></div></div><BarrierList audit={audit} onReplay={setReplay} /></section>
          <section className="section"><div className="section-heading"><div><h2>Effort compared with the baseline</h2><p>The same journey, the same agent, one impairment.</p></div></div><EffortChart audit={audit} />{!audit.scores?.perspectives && <div className="quiet-note">Appears when two perspectives have finished.</div>}</section>
        </div>
        <StaticCard audit={audit} />
        {fixCount > 0 && <button className="fix-teaser" onClick={() => setTab('fixes')}><Sparkles size={18} /><span><strong>{fixCount} suggested fix{fixCount > 1 ? 'es' : ''}, with before/after previews</strong><small>{audit.parent ? 'Compare this re-evaluation with the original audit.' : 'See each fix as the blocked user would, then evaluate again with the fixes applied.'}</small></span><ArrowUpRight size={16} /></button>}
        <p className="report-footnote">Simulated users driven by H computer-use agents, not an accessibility certification. Technical errors are excluded from scores.</p>
      </>}
      {tab === 'fixes' && <FixesPanel audit={audit} onOpen={onOpen} />}
      {tab === 'activity' && <section className="activity-panel"><h2>Activity</h2><div className="event-list">{audit.events?.length ? audit.events.map(e => <div className="event-item" key={e.id}><span className={`event-dot ${e.kind}`} /><time>{new Date(e.created * 1000).toLocaleTimeString('en-GB')}</time><p>{e.message}</p></div>) : <p>Preparing the event feed…</p>}</div></section>}
    </div>
    {replay && <ReplayDialog audit={audit} run={replay} close={closeReplay} others={replayable} open={setReplay} />}
    {stopConfirm && <Dialog close={closeConfirm} title="Stop this audit?"><div className="confirm-dialog"><span className="empty-icon"><Square size={22} /></span><h2>Stop every agent?</h2><p>We ask H to cancel every active session and skip the remaining runs. Results collected so far stay in your history.</p><div className="confirm-actions"><button className="button secondary" onClick={closeConfirm}>Keep exploring</button><button className="button danger" disabled={busy} onClick={() => void control('stop')}>{busy ? <Spinner /> : <Square size={13} />}Stop audit</button></div></div></Dialog>}
  </>;
}

