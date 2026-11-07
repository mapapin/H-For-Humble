import React, { useState } from 'react';
import { ArrowRight, ArrowUpRight, CheckCircle2, Play, RotateCcw, Sparkles, Wrench, X } from 'lucide-react';
import { api, Dialog, ErrorNote, pct, pname, PersonaIcon, Spinner, Status, terminal } from './shared';
import type { Audit, Comparison, Fix, Fixes, Summary } from './shared';

const score = (rows: Summary[] = []) => {
  const valid = rows.filter(r => r.success_rate != null);
  return valid.length ? Math.round(100 * valid.reduce((n, r) => n + r.success_rate!, 0) / valid.length) : null;
};
const rate = (rows: Summary[] = [], persona: string) => {
  const valid = rows.filter(r => r.persona === persona && r.success_rate != null);
  return valid.length ? valid.reduce((n, r) => n + r.success_rate!, 0) / valid.length : null;
};

/** Before/after image comparison. The range input is the control: arrow keys move the divider. */
export function CompareSlider({ before, after, label, split = 50 }: { before: string; after: string; label: string; split?: number }) {
  const [pos, setPos] = useState(split);  // starts on the change (computed when the images were cropped)
  return <div className="compare" style={{ '--pos': `${pos}%` } as React.CSSProperties}>
    <img src={before} alt={`Before: ${label}`} />
    <img className="compare-after" src={after} alt={`After: ${label}`} />
    <span className="compare-line" aria-hidden="true" />
    <span className="compare-tag before">Before</span><span className="compare-tag after">After</span>
    <input type="range" min={0} max={100} value={pos} onChange={e => setPos(Number(e.target.value))} aria-label={`Compare before and after: ${label}`} />
  </div>;
}

export function FixesPanel({ audit, onOpen }: { audit: Audit; onOpen: (id: string) => void }) {
  const source = audit.parent && !audit.fixes?.patch?.fixes?.length ? audit.parent : audit;
  const fixes: Fixes | undefined = source.fixes?.patch?.fixes?.length ? source.fixes : undefined;
  const imageBase = `/api/audits/${source.id}/fixes/`;
  const [ask, setAsk] = useState(false);
  const isReevaluation = !!audit.parent_id;
  const canReevaluate = !isReevaluation && audit.config.mode !== 'auto' && terminal(audit.status) && !!fixes;
  return <div className="fixes-panel">
    {audit.parent && <BeforeAfterScores parent={audit.parent} audit={audit} onOpen={onOpen} />}
    {!!audit.rounds?.length && <Rounds audit={audit} />}
    {!fixes ? <div className="quiet-note">{!terminal(audit.status) ? 'Suggested fixes appear when the audit finishes.' : audit.summary.every(r => r.success_rate === 1) ? 'Every perspective passed: no fix to suggest.' : 'No fix suggestions for this audit.'}</div> : <>
      <section className="section fixes-intro"><div className="section-heading"><div>
        <h2><Sparkles size={18} /> {audit.config.mode === 'auto' ? 'Fixes applied by the loop' : isReevaluation ? 'Fixes evaluated in this run' : 'Suggested fixes'} <span className="number-pill">{fixes.patch.fixes.length}</span></h2>
        <p>{fixes.source === 'devin' ? <>Written by Devin from the barriers and the pages each perspective saw{fixes.devin_session_url && <> · <a href={fixes.devin_session_url} target="_blank" rel="noreferrer">Open the Devin session<ArrowUpRight size={12} /></a></>}.</> : 'Demo fixes in the same format Devin returns (Devin is used when DEVIN_API_KEY is configured).'} Previews are injected in the page; the real change for developers is listed under each fix.</p>
      </div>{canReevaluate && <button className="button primary" onClick={() => setAsk(true)}><RotateCcw size={15} />Evaluate again with fixes</button>}</div>
        {canReevaluate && <p className="fix-note">Re-runs every perspective with these fixes applied, then compares the scores. This takes as long as the audit itself.</p>}
        {!!audit.reevaluations?.length && <div className="reeval-list">{audit.reevaluations.map(r => <button key={r.id} className="reeval-row" onClick={() => onOpen(r.id)}><CheckCircle2 size={16} /><span><strong>{r.name}</strong><small>Score {score(audit.summary) ?? '—'} → {score(r.summary) ?? '—'}</small></span><Status value={r.status} /><ArrowRight size={15} /></button>)}</div>}
      </section>
      <PersonaComparisons comparisons={fixes.comparisons.filter(c => c.kind === 'persona' && c.before)} base={imageBase} />
      <section className="section"><div className="section-heading"><div><h2>Per criterion</h2><p>Each fix on the page where it applies, seen as the perspective it unblocks.</p></div></div>
        {fixes.patch.fixes.map(f => <FixCard key={f.id} fix={f} comparison={fixes.comparisons.find(c => c.kind === 'fix' && c.id === f.id)} base={imageBase} />)}</section>
    </>}
    {ask && <ReevaluateDialog audit={audit} close={() => setAsk(false)} onStarted={a => { setAsk(false); onOpen(a.id); }} />}
  </div>;
}

function PersonaComparisons({ comparisons, base }: { comparisons: Comparison[]; base: string }) {
  if (!comparisons.length) return null;
  return <section className="section"><div className="section-heading"><div><h2>Before and after, per perspective</h2><p>All fixes applied, on the page where each perspective got stuck. Drag the divider.</p></div></div>
    <div className="compare-grid">{comparisons.map(c => <figure key={c.id}><CompareSlider before={base + c.before} after={base + c.after} label={pname(c.persona)} split={c.split} /><figcaption><PersonaIcon id={c.persona} size={14} />{pname(c.persona)}</figcaption></figure>)}</div></section>;
}

function FixCard({ fix, comparison, base }: { fix: Fix; comparison?: Comparison; base: string }) {
  return <article className="fix-card"><div className="fix-meta">
    <span className="wcag-tag">WCAG {fix.wcag}</span><h3>{fix.title}</h3>
    <p className="fix-who">{fix.personas.map(p => <span key={p}><PersonaIcon id={p} size={12} />{pname(p)}</span>)}<small>{fix.page_url.replace(/^https?:\/\//, '')}</small></p>
    <p>{fix.description}</p>
    <details><summary><Wrench size={13} />Code change for developers</summary><p>{fix.code_change}</p>{(fix.css || fix.js) && <pre>{[fix.css, fix.js].filter(Boolean).join('\n\n')}</pre>}</details>
  </div>{comparison?.before ? <CompareSlider before={base + comparison.before} after={base + comparison.after!} label={fix.title} split={comparison.split} /> : <div className="quiet-note">No snapshot of this page in the audit.</div>}</article>;
}

function BeforeAfterScores({ parent, audit, onOpen }: { parent: NonNullable<Audit['parent']>; audit: Audit; onOpen: (id: string) => void }) {
  return <section className="section"><div className="section-heading"><div><h2>With fixes: score {score(parent.summary) ?? '—'} → {score(audit.summary) ?? '—'}</h2><p>Same journeys and perspectives as <button className="text-button" onClick={() => onOpen(parent.id)}>{parent.name}</button>, with its {parent.fixes?.patch?.fixes?.length || 0} suggested fixes injected in every page.</p></div></div>
    <div className="table-wrap"><table className="breakdown-table"><thead><tr><th>Perspective</th><th>Before</th><th>With fixes</th></tr></thead><tbody>{audit.config.personas.map(p => {
      const b = rate(parent.summary, p), a = rate(audit.summary, p);
      return <tr key={p}><td><strong>{pname(p)}</strong></td><td>{pct(b)}</td><td className={a != null && b != null && a > b ? 'improved' : ''}>{pct(a)}</td></tr>;
    })}</tbody></table></div></section>;
}

function Rounds({ audit }: { audit: Audit }) {
  const rounds = audit.rounds!;
  const last = rounds[rounds.length - 1];
  return <section className="section"><div className="section-heading"><div><h2>Automatic fix loop: score {rounds[0].score} → {last.score}</h2><p>{last.passed ? `Every perspective passed after ${rounds.length} round${rounds.length > 1 ? 's' : ''}.` : last.stopped || `Stopped after ${rounds.length} rounds (limit ${audit.config.max_rounds}).`}</p></div></div>
    <div className="table-wrap"><table className="breakdown-table"><thead><tr><th>Round</th><th>Score</th>{audit.config.personas.map(p => <th key={p}>{pname(p)}</th>)}<th>Fixes applied</th></tr></thead><tbody>{rounds.map(r => <tr key={r.round}><td><strong>{r.round + 1}</strong>{r.passed && <CheckCircle2 size={14} className="passed-icon" />}</td><td>{r.score}</td>{audit.config.personas.map(p => <td key={p}>{pct(r.rates[p])}</td>)}<td>{r.fixes_applied.length ? r.fixes_applied.join(', ') : 'none'}</td></tr>)}</tbody></table></div></section>;
}

function ReevaluateDialog({ audit, close, onStarted }: { audit: Audit; close: () => void; onStarted: (a: Audit) => void }) {
  const manual = !audit.config.vault_id;
  const needLogin = manual && !!audit.config.uses_login, needHeaders = manual && !!audit.config.uses_headers;
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [headers, setHeaders] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function start() {
    setBusy(true); setError('');
    try {
      const parsed = Object.fromEntries(headers.split('\n').map(l => l.split(/:(.*)/s).map(s => s.trim())).filter(([k, v]) => k && v));
      onStarted(await api<Audit>(`/audits/${audit.id}/reevaluate`, { method: 'POST', body: JSON.stringify({ credentials: { username, password, headers: parsed } }) }));
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  return <Dialog close={close} title="Evaluate again with fixes"><div className="confirm-dialog">
    <button className="icon-button dialog-close" aria-label="Close" onClick={close}><X size={18} /></button>
    <span className="empty-icon"><RotateCcw size={22} /></span><h2>Evaluate again with fixes?</h2>
    <p>Every perspective runs the same journeys again, with the {audit.fixes?.patch.fixes.length} suggested fixes injected in each page. You will get a before/after score.</p>
    {needLogin && <><label>Website login<input value={username} onChange={e => setUsername(e.target.value)} autoComplete="off" /></label><label>Password<input type="password" value={password} onChange={e => setPassword(e.target.value)} autoComplete="off" /></label></>}
    {needHeaders && <label>Access headers <small>one per line, Name: value</small><textarea rows={2} value={headers} onChange={e => setHeaders(e.target.value)} /></label>}
    {(needLogin || needHeaders) && <p className="fix-note">Manual credentials are never stored, so we need them again.</p>}
    {error && <ErrorNote message={error} />}
    <div className="confirm-actions"><button className="button secondary" onClick={close}>Cancel</button><button className="button primary" disabled={busy} onClick={() => void start()}>{busy ? <Spinner /> : <Play size={14} />}Start re-evaluation</button></div>
  </div></Dialog>;
}
