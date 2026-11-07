import React, { useState } from 'react';
import { AlertOctagon, ArrowUpRight, BookOpenCheck, CircleCheck, CircleX, Clock3, Footprints, Info, Loader2, Play, Radio, Scale, TriangleAlert, X } from 'lucide-react';
import { Dialog, duration, PERSONAS, pname, PersonaIcon, ScoreRing, technical, terminal } from './shared';
import type { Audit, Lane, PerspectiveScore, Run } from './shared';

const short = (id: string) => PERSONAS.find(p => p.id === id)?.short || '';
const times = (n: number | null | undefined) => n == null ? null : `${n.toFixed(1).replace(/\.0$/, '')}×`;

export function latestLane(audit: Audit, persona: string): [string, Lane] | null {
  const lanes = Object.entries(audit.progress?.lanes || {}).filter(([, l]) => l.persona === persona);
  lanes.sort((a, b) => (b[1].started_at || 0) - (a[1].started_at || 0));
  return lanes[0] || null;
}

/** The headline: one number, one sentence, the few facts behind it. */
export function ScoreHero({ audit }: { audit: Audit }) {
  const s = audit.scores || {};
  const per = s.perspectives || {};
  const active = !terminal(audit.status);
  const task = audit.config.tasks.length === 1 ? `“${audit.config.tasks[0].title}”` : `the ${audit.config.tasks.length} journeys`;
  const blocked = Object.entries(per).filter(([k, p]) => k !== 'baseline' && p.completion === 0).map(([k]) => pname(k));
  const struggling = Object.entries(per).filter(([k, p]) => k !== 'baseline' && p.completion && (p.steps_vs_baseline || 0) >= 1.5);
  const worst = Object.entries(per).filter(([k]) => k !== 'baseline').sort((a, b) => (b[1].steps_vs_baseline || 0) - (a[1].steps_vs_baseline || 0))[0];
  const barriers = Object.values(per).reduce((n, p) => n + p.barriers, 0), blocking = Object.values(per).reduce((n, p) => n + p.blocking, 0);
  const results = audit.results || [];
  const agentTime = results.reduce((n, r) => n + (r.agent_duration_s || 0), 0);
  const cost = results.reduce((n, r) => n + (r.metrics?.cost_usd || 0), 0);
  let sentence: React.ReactNode;
  if (s.score == null) sentence = active ? <>Agents are exploring. The score appears as soon as the first perspective finishes.</> : <>No perspective could be scored.</>;
  else if (!s.baseline_ok && per.baseline?.tested) sentence = <>Even the baseline user could not complete {task}. Check the journey goal, or the site itself.</>;
  else sentence = <><b>{s.can_complete} of {s.tested_perspectives}</b> perspectives completed {task}{blocked.length ? <>. <span className="blocked-names">Blocked: {blocked.join(', ')}.</span></> : '.'}{struggling.length ? <> {struggling.map(([k]) => pname(k)).join(', ')} needed far more effort.</> : null}</>;
  return <section className="score-hero" aria-label="Audit score">
    <ScoreRing score={s.score} grade={s.grade} size={156} stroke={12} label="Inclusion score" />
    <div className="score-hero-copy">
      <span className="eyebrow">INCLUSION SCORE {active && <span className="live-pill"><span className="live-dot" />LIVE</span>}</span>
      <h2>{sentence}</h2>
      <ScoreExplainer />
    </div>
    <dl className="hero-facts">
      <div><dt><TriangleAlert size={14} />Barriers</dt><dd>{barriers}</dd><small>{blocking} blocking</small></div>
      <div><dt><Footprints size={14} />Extra effort</dt><dd>{times(worst?.[1].steps_vs_baseline) || '—'}</dd><small>{worst?.[1].steps_vs_baseline ? `steps for ${pname(worst[0]).toLowerCase()}` : 'vs baseline'}</small></div>
      <div><dt><Radio size={14} />Agents</dt><dd>{results.length}<span>/{audit.config.tasks.length * audit.config.personas.length * audit.config.repetitions}</span></dd><small>{duration(agentTime)} of browsing{cost ? ` · $${cost.toFixed(2)}` : ''}</small></div>
    </dl>
  </section>;
}

function ScoreExplainer() {
  return <details className="score-explainer"><summary><Info size={13} />How the score works</summary>
    <p>Each run scores 0 when the user could not finish the journey. A finished journey starts at 100 and loses points for extra steps and time compared with the baseline user (up to −30 and −15), for retried or blocked actions (up to −15) and for every barrier the agent reported (up to −30). A perspective is the mean of its runs; the audit is the mean of its perspectives. A ≥ 90 · B ≥ 75 · C ≥ 60 · D ≥ 40 · F below.</p></details>;
}

/** Linkup: what the site declares about its accessibility, next to what the agents measured. */
export function ContextCard({ audit }: { audit: Audit }) {
  const c = audit.context;
  if (!c || !c.declared_compliance) return null;
  const s = audit.scores || {};
  return <section className="context-card" aria-label="Declared accessibility">
    <div className="context-col"><span className="eyebrow"><BookOpenCheck size={14} />DECLARED{c.organisation ? ` BY ${c.organisation.toUpperCase()}` : ''}</span>
      <p className="context-claim">{c.declared_compliance}</p>
      <small>{[c.audit_date && `Audit ${c.audit_date}`, c.self_assessed && 'self-assessed'].filter(Boolean).join(' · ')}{c.accessibility_statement_url && <> · <a href={c.accessibility_statement_url} target="_blank" rel="noreferrer">Accessibility statement<ArrowUpRight size={11} /></a></>}</small></div>
    <div className="context-vs" aria-hidden="true">vs</div>
    <div className="context-col"><span className="eyebrow"><Scale size={14} />MEASURED BY HUMBLE</span>
      <p className="context-claim">{s.score == null ? 'Measuring…' : `${s.impaired_blocked || 0} of ${(s.tested_perspectives || 1) - 1} impaired perspectives blocked · score ${s.score}/100`}</p>
      {c.legal_context && <small>{c.legal_context}</small>}</div>
    <p className="context-source">Context found by <b>Linkup</b> web search{c.sources?.length ? <>: {c.sources.slice(0, 3).map((src, i) => <a key={i} href={src.url} target="_blank" rel="noreferrer">{src.name || new URL(src.url).host}</a>)}</> : null}</p>
  </section>;
}

/** Every perspective side by side: live while agents run (screen + thought), then outcome, metrics and replay. */
export function PerspectiveGrid({ audit, onReplay }: { audit: Audit; onReplay: (r: Run) => void }) {
  const [watching, setWatching] = useState<string | null>(null);
  const close = React.useCallback(() => setWatching(null), []);
  return <><div className={`perspective-grid ${terminal(audit.status) ? '' : 'live'}`}>{audit.config.personas.map(p => <PerspectiveTile key={p} audit={audit} persona={p} onReplay={onReplay} onWatch={() => setWatching(p)} />)}</div>
    {watching && <LiveDialog audit={audit} persona={watching} close={close} />}</>;
}

/** One agent, full size, while it runs: its live screen and what it is thinking. */
function LiveDialog({ audit, persona, close }: { audit: Audit; persona: string; close: () => void }) {
  const lane = latestLane(audit, persona);
  const l = lane?.[1];
  const src = l?.preview_at ? `/api/audits/${audit.id}/files/live-${lane![0]}.png?t=${l.preview_at}` : null;
  return <Dialog close={close} title={`Live: ${pname(persona)}`} wide><div className="live-dialog">
    <header className="replay-head"><PersonaIcon id={persona} size={18} /><div><h2>{pname(persona)} <span className="live-pill"><span className="live-dot" />LIVE</span></h2><p>{short(persona)} · step {l?.step || 0} · {l?.actions || 0} browser actions</p></div>
      <div className="replay-head-actions">{l?.agent_view_url && <a className="button secondary" href={l.agent_view_url} target="_blank" rel="noreferrer">H Agent View<ArrowUpRight size={14} /></a>}<button className="icon-button" aria-label="Close live view" onClick={close}><X size={18} /></button></div></header>
    <div className="live-dialog-body">{src ? <img src={src} alt={`Live browser of the ${pname(persona)} agent`} /> : <div className="p-screen-empty"><Loader2 className="spin" size={20} /><span>Waiting for the first screen…</span></div>}
      <div className="replay-caption" aria-live="polite"><span className="step-no">Step {l?.step || 0}</span><div><p>{l?.thought || l?.message || 'Connecting to H…'}</p>{l?.action && <span className="action-chip">{l.action}</span>}</div></div></div>
    {l?.state === 'finished' && <p className="quiet-note">This run has finished. Close this view to open its replay.</p>}
  </div></Dialog>;
}

function PerspectiveTile({ audit, persona, onReplay, onWatch }: { audit: Audit; persona: string; onReplay: (r: Run) => void; onWatch: () => void }) {
  const records = (audit.results || []).filter(r => r.persona === persona);
  const score: PerspectiveScore | undefined = audit.scores?.perspectives?.[persona];
  const lane = latestLane(audit, persona);
  const running = !terminal(audit.status) && lane && ['starting', 'running'].includes(lane[1].state || '');
  const waiting = !terminal(audit.status) && lane?.[1].state === 'waiting';
  const planned = audit.config.tasks.length * audit.config.repetitions;
  const last = records[records.length - 1];
  const shown = last && !running ? last : undefined;
  const frames = shown?.replay?.frames?.filter(f => f.file) || [];
  const still = shown?.final_screenshot ? `/api/audits/${audit.id}/files/${shown.final_screenshot}` : frames.length ? `/api/audits/${audit.id}/${frames[frames.length - 1].file}` : null;
  const liveSrc = running && lane![1].preview_at ? `/api/audits/${audit.id}/files/live-${lane![0]}.png?t=${lane![1].preview_at}` : null;
  const outcome = !records.length ? null : records.every(technical) ? 'error' : score?.completion === 1 ? 'pass' : score?.completion ? 'partial' : 'fail';
  const state = running ? 'live' : outcome || (terminal(audit.status) ? 'skipped' : 'queued');
  return <article className={`p-tile state-${state} ${persona === 'baseline' ? 'is-baseline' : ''}`}>
    <header><PersonaIcon id={persona} size={16} /><div><h3>{pname(persona)}</h3><small>{persona === 'baseline' ? 'Reference user' : short(persona)}</small></div>
      {running ? <span className="live-pill"><span className="live-dot" />LIVE</span> : score?.grade ? <span className={`grade-badge grade-${score.grade}`} aria-label={`Grade ${score.grade}`}>{score.grade}</span> : null}</header>
    <div className="p-screen">
      {liveSrc ? <img src={liveSrc} alt={`Live browser of the ${pname(persona)} agent`} /> : still ? <img src={still} alt={`Final screen of the ${pname(persona)} run`} /> : <div className="p-screen-empty">{waiting ? <><Clock3 size={20} /><span>Waiting for a free H slot</span><small>Starts as soon as H releases one</small></> : running ? <><Loader2 className="spin" size={20} /><span>Opening a browser…</span></> : terminal(audit.status) ? <span>Not run</span> : <><Clock3 size={20} /><span>Queued</span></>}</div>}
      {running && <div className="p-caption"><span className="step-badge">Step {lane![1].step || 0}</span><p>{lane![1].thought || lane![1].message || 'Connecting to H…'}</p></div>}
      {shown && <button className="p-replay" onClick={() => onReplay(shown)} aria-label={`Replay the ${pname(persona)} run`}><Play size={16} />Replay</button>}
      {running && <button className="p-replay" onClick={onWatch} aria-label={`Watch the ${pname(persona)} agent live`}><Radio size={16} />Watch live</button>}
    </div>
    <footer>
      {outcome && <span className={`outcome-chip ${outcome}`}>{outcome === 'pass' ? <CircleCheck size={13} /> : outcome === 'error' ? <AlertOctagon size={13} /> : <CircleX size={13} />}{outcome === 'pass' ? 'Completed' : outcome === 'partial' ? `${score!.completed}/${score!.tested} completed` : outcome === 'error' ? 'Technical error' : 'Blocked'}</span>}
      {!outcome && <span className="outcome-chip neutral">{running ? 'Exploring' : waiting ? 'Waiting for H' : terminal(audit.status) ? 'Not run' : 'Queued'}</span>}
      {score && score.tested > 0 && <dl className="p-metrics">
        <div><dt>Steps</dt><dd>{score.steps ?? '—'}{score.steps_vs_baseline && score.steps_vs_baseline >= 1.15 ? <em>{times(score.steps_vs_baseline)}</em> : null}</dd></div>
        <div><dt>Time</dt><dd>{duration(score.time_s)}</dd></div>
        <div><dt>Barriers</dt><dd>{score.barriers}</dd></div>
        <div><dt>Score</dt><dd><b>{score.score ?? '—'}</b></dd></div>
      </dl>}
      {planned > 1 && <small className="p-count">{records.length}/{planned} runs</small>}
    </footer>
  </article>;
}

const SEVERITY = { blocking: 0, major: 1, minor: 2 } as Record<string, number>;

/** What stopped users, worst first, each one linked to the run where it happened. */
export function BarrierList({ audit, onReplay }: { audit: Audit; onReplay: (r: Run) => void }) {
  const [all, setAll] = useState(false);
  const items = (audit.results || []).flatMap(r => (r.report?.obstacles || []).map(o => ({ o, r })))
    .sort((a, b) => (SEVERITY[a.o.severity] ?? 3) - (SEVERITY[b.o.severity] ?? 3) || Number(a.r.persona === 'baseline') - Number(b.r.persona === 'baseline'));
  const fixes = audit.fixes?.patch?.fixes || audit.parent?.fixes?.patch?.fixes || [];
  if (!items.length) return <div className="quiet-note">{(audit.results || []).length ? 'No barrier reported by the agents.' : 'Barriers appear here as agents finish their journeys.'}</div>;
  const shown = all ? items : items.slice(0, 6);
  return <>
    <ul className="barrier-list">{shown.map(({ o, r }, i) => {
      const fix = fixes.find(f => f.personas.includes(r.persona));
      return <li key={i} className={`barrier sev-${o.severity}`}>
        <span className={`severity ${o.severity}`}>{o.severity}</span>
        <div className="barrier-body"><p>{o.what}</p><small><PersonaIcon id={r.persona} size={11} />{pname(r.persona)} · {o.where}{fix ? <> · <span className="wcag-tag">WCAG {fix.wcag}</span></> : null}</small></div>
        <button className="text-button" onClick={() => onReplay(r)}><Play size={13} />Replay</button>
      </li>;
    })}</ul>
    {items.length > 6 && <button className="button secondary show-more" onClick={() => setAll(!all)}>{all ? 'Show fewer' : `Show all ${items.length} barriers`}</button>}
  </>;
}

/** Steps and time per perspective against the baseline user: effort is the cost of a barrier you can work around. */
export function EffortChart({ audit }: { audit: Audit }) {
  const per = audit.scores?.perspectives || {};
  const rows = audit.config.personas.map(p => [p, per[p]] as const).filter(([, s]) => s && s.tested > 0);
  if (rows.length < 2) return null;
  const maxSteps = Math.max(...rows.map(([, s]) => s!.steps || 0), 1), maxTime = Math.max(...rows.map(([, s]) => s!.time_s || 0), 1);
  const base = per.baseline;
  return <div className="effort-chart" role="table" aria-label="Effort per perspective">
    <div className="effort-row effort-head" role="row"><span role="columnheader">Perspective</span><span role="columnheader">Agent steps</span><span role="columnheader">Time</span><span role="columnheader">Outcome</span></div>
    {rows.map(([p, s]) => <div className="effort-row" role="row" key={p}>
      <span role="cell" className="effort-name"><PersonaIcon id={p} size={12} />{pname(p)}</span>
      <span role="cell" className="effort-bar-cell"><span className={`effort-bar ${s!.completion ? 'ok' : 'ko'}`} style={{ width: `${100 * (s!.steps || 0) / maxSteps}%` }} />{base?.steps && <span className="baseline-mark" style={{ left: `${100 * base.steps / maxSteps}%` }} title="Baseline" />}<b>{s!.steps ?? '—'}</b>{s!.steps_vs_baseline && s!.steps_vs_baseline >= 1.15 ? <em>{times(s!.steps_vs_baseline)}</em> : null}</span>
      <span role="cell" className="effort-bar-cell"><span className={`effort-bar time ${s!.completion ? 'ok' : 'ko'}`} style={{ width: `${100 * (s!.time_s || 0) / maxTime}%` }} />{base?.time_s && <span className="baseline-mark" style={{ left: `${100 * base.time_s / maxTime}%` }} />}<b>{duration(s!.time_s)}</b></span>
      <span role="cell">{s!.completion === 1 ? <span className="outcome-chip pass"><CircleCheck size={12} />Done</span> : s!.completion ? <span className="outcome-chip partial">{Math.round(100 * s!.completion)}%</span> : <span className="outcome-chip fail"><CircleX size={12} />Blocked</span>}</span>
    </div>)}
    <p className="effort-legend"><span className="baseline-mark static" />Baseline user. Bars in red: the journey was not completed (steps until the agent gave up).</p>
  </div>;
}

const wcagLabel = (tag: string) => { const d = tag.replace('wcag', ''); return d.length >= 3 ? `${d[0]}.${d[1]}.${d.slice(2)}` : d.toUpperCase(); };

/** A static checker (axe-core) on the same pages, against what the agents lived: which findings stop people,
 * and which blockers no rule catches. */
export function StaticCard({ audit }: { audit: Audit }) {
  const scan = audit.static_scan;
  if (!scan || !scan.totals) return null;
  const per = audit.scores?.perspectives || {};
  const blocked = Object.entries(per).filter(([k, p]) => k !== 'baseline' && p.completion === 0).map(([k]) => k);
  const covered = new Set((scan.violations || []).flatMap(v => v.personas));
  const missed = blocked.filter(p => !covered.has(p));
  const top = (scan.violations || []).slice(0, 4);
  return <section className="static-card" aria-label="Static checker comparison">
    <div className="static-head">
      <div><span className="eyebrow">STATIC CHECKER VS REAL JOURNEYS</span>
        <h3>axe-core flags <b>{scan.totals.rules} rule{scan.totals.rules === 1 ? '' : 's'}</b> on the {scan.pages?.length || 0} pages the agents visited. {blocked.length ? <>Our agents found <b>{blocked.length} perspective{blocked.length > 1 ? 's' : ''} blocked</b>{missed.length ? <>, {missed.length === blocked.length ? 'none' : `${missed.length}`} of which no rule detects: <span className="blocked-names">{missed.map(pname).join(', ')}</span>.</> : ', each one related to a flagged rule.'}</> : 'No perspective was blocked on this journey.'}</h3></div>
    </div>
    {top.length > 0 && <ul className="static-list">{top.map(v => <li key={v.id}>
      <span className={`impact impact-${v.impact || 'minor'}`}>{v.impact || 'minor'}</span>
      <span className="static-rule"><a href={v.helpUrl} target="_blank" rel="noreferrer">{v.help}</a><small>{v.id} · {v.nodes} element{v.nodes > 1 ? 's' : ''}{v.wcag.length ? ` · WCAG ${v.wcag.map(wcagLabel).join(', ')}` : ''}</small></span>
      {v.blocked_someone ? <span className="outcome-chip fail">Blocked {v.personas.filter(p => blocked.includes(p)).map(pname).join(', ')}</span> : v.related_to_barrier ? <span className="outcome-chip partial">Related to a barrier</span> : <span className="outcome-chip neutral">No barrier seen</span>}
    </li>)}</ul>}
    <p className="static-note">Static rules check the code; Humble's agents live the journey. Use both: the agents tell you which findings actually stop people, and what no rule can see.</p>
  </section>;
}
