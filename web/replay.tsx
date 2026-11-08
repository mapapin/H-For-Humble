import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Ban, ChevronLeft, ChevronRight, Hand, Keyboard, MousePointerClick, Pause, Play, ScrollText, Type, X } from 'lucide-react';
import { Dialog, duration, pname, PersonaIcon, technical } from './shared';
import type { Audit, ReplayStep, Run } from './shared';

const SPEEDS = [1, 2, 4];
const KEY_NAMES: Record<string, string> = { return: 'Enter', enter: 'Enter', tab: 'Tab', shift: 'Shift', space: 'Space', ' ': 'Space', escape: 'Esc', esc: 'Esc', backspace: '⌫', arrowdown: '↓', down: '↓', arrowup: '↑', up: '↑', arrowleft: '←', left: '←', arrowright: '→', right: '→', ctrl: 'Ctrl', control: 'Ctrl', alt: 'Alt', meta: '⌘', pagedown: 'Page ↓', pageup: 'Page ↑', home: 'Home', end: 'End' };
export const keyName = (k: string) => KEY_NAMES[k.toLowerCase()] || (k.length === 1 ? k.toUpperCase() : k.charAt(0).toUpperCase() + k.slice(1));
const STEP_MS = 1600;

function ActionIcon({ kind }: { kind?: string }) {
  const Icon = kind === 'click' ? MousePointerClick : kind === 'keys' ? Keyboard : kind === 'type' || kind === 'credential' ? Type : kind === 'answer' ? ScrollText : Hand;
  return <Icon size={14} aria-hidden="true" />;
}

export function outcomeLabel(r: Run) { return technical(r) ? 'Technical error' : r.success ? 'Completed' : 'Not completed'; }

/** A run, step by step: the screenshot the agent decided on, its thought, its action, and what the simulated
 * impairment did to it (tremor offset, blocked mouse). Plays like a video; arrows and space work too. */
export function ReplayDialog({ audit, run, close, others = [], open }: { audit: Audit; run: Run; close: () => void; others?: Run[]; open?: (r: Run) => void }) {
  const steps = run.replay?.steps || [];
  const frames = run.replay?.frames || [];
  const [index, setIndex] = useState(0), [playing, setPlaying] = useState(steps.length > 1), [speed, setSpeed] = useState(1);
  const listRef = useRef<HTMLOListElement>(null);
  useEffect(() => { setIndex(0); setPlaying(steps.length > 1); }, [run.tag]);  // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (!playing) return;
    if (index >= steps.length - 1) { setPlaying(false); return; }
    const t = setTimeout(() => setIndex(i => Math.min(i + 1, steps.length - 1)), STEP_MS / speed);
    return () => clearTimeout(t);
  }, [playing, index, speed, steps.length]);
  useEffect(() => { listRef.current?.querySelector(`[data-step="${index}"]`)?.scrollIntoView({ block: 'nearest' }); }, [index]);
  useEffect(() => {  // preload the next frames so playback never flashes
    for (const s of steps.slice(index + 1, index + 4)) { const f = s.frame != null ? frames[s.frame] : undefined; if (f?.file) { const img = new Image(); img.src = `/api/audits/${audit.id}/${f.file}`; } }
  }, [index, steps, frames, audit.id]);
  const step: ReplayStep | undefined = steps[index];
  const frameIndex = useMemo(() => { for (let i = index; i >= 0; i--) if (steps[i]?.frame != null) return steps[i].frame!; return null; }, [index, steps]);
  const frame = frameIndex != null ? frames[frameIndex] : undefined;
  const src = frame?.file ? `/api/audits/${audit.id}/${frame.file}` : run.final_screenshot ? `/api/audits/${audit.id}/files/${run.final_screenshot}` : null;
  const task = audit.config.tasks.find(t => t.id === run.task);
  const blockedCount = steps.filter(s => s.effects?.blocked).length;
  const jitterCount = steps.filter(s => s.effects?.jitter).length;
  function onKey(e: React.KeyboardEvent) {
    if (e.target instanceof HTMLInputElement || e.target instanceof HTMLSelectElement) return;
    if (e.key === 'ArrowRight') { e.preventDefault(); setPlaying(false); setIndex(i => Math.min(i + 1, steps.length - 1)); }
    if (e.key === 'ArrowLeft') { e.preventDefault(); setPlaying(false); setIndex(i => Math.max(i - 1, 0)); }
    if (e.key === ' ' && !(e.target instanceof HTMLButtonElement)) { e.preventDefault(); setPlaying(p => !p); }
  }
  const atEnd = index === steps.length - 1;
  return <Dialog close={close} title={`Replay: ${pname(run.persona)}`} wide>
    <div className="replay" onKeyDown={onKey}>
      <header className="replay-head">
        <PersonaIcon id={run.persona} size={18} />
        <div><h2>{pname(run.persona)} <span className={`outcome-chip ${technical(run) ? 'error' : run.success ? 'pass' : 'fail'}`}>{outcomeLabel(run)}</span></h2>
          <p>{task?.title || run.task}{audit.config.repetitions > 1 ? ` · run ${run.run + 1}` : ''} · {run.steps ?? steps.length} steps · {duration(run.agent_duration_s)}{blockedCount ? ` · ${blockedCount} mouse action${blockedCount > 1 ? 's' : ''} blocked` : ''}{jitterCount ? ` · ${jitterCount} click${jitterCount > 1 ? 's' : ''} shaken by tremor` : ''}{run.assisted ? ' · assisted (you sent a hint)' : ''}</p></div>
        <div className="replay-head-actions">
          {others.length > 1 && open && <select aria-label="Replay another perspective" value={run.tag} onChange={e => { const next = others.find(o => o.tag === e.target.value); if (next) open(next); }}>{others.map(o => <option key={o.tag} value={o.tag}>{pname(o.persona)}{audit.config.repetitions > 1 ? ` · run ${o.run + 1}` : ''}</option>)}</select>}
          {run.agent_view_url && <a className="button secondary" href={run.agent_view_url} target="_blank" rel="noreferrer">H Agent View<ArrowUpRight size={14} /></a>}
          <button className="icon-button" aria-label="Close replay" onClick={close}><X size={18} /></button>
        </div>
      </header>
      {!steps.length ? <div className="replay-empty">{src ? <img src={src} alt="Final screen of this run" /> : null}<p>No step-by-step replay was recorded for this run{run.agent_view_url ? '. Open it in H Agent View for the full trajectory.' : '.'}</p></div> : <div className="replay-body">
        <div className="replay-stage">
          <div className="replay-frame">
            {src && <img src={src} alt={`Step ${index + 1}: what the ${pname(run.persona)} agent saw`} />}
            {step?.action?.x != null && step.action.y != null && <ClickMarker step={step} w={frame?.w} h={frame?.h} />}
            {step?.action?.kind === 'keys' && <div className="keycaps" aria-hidden="true">{step.action.keys?.map((k, i) => <kbd key={i}>{keyName(k)}</kbd>)}</div>}
            {step?.effects?.blocked && <div className="replay-flag blocked"><Ban size={15} />Mouse blocked: this user cannot use a mouse</div>}
            {step?.effects?.jitter && !step.effects.blocked && <div className="replay-flag tremor"><Hand size={15} />Tremor: the click landed {Math.abs(step.effects.jitter[0])}px {step.effects.jitter[0] >= 0 ? 'right' : 'left'}, {Math.abs(step.effects.jitter[1])}px {step.effects.jitter[1] >= 0 ? 'down' : 'up'} of the target</div>}
          </div>
          <div className="replay-caption" aria-live="polite">
            <span className="step-no">Step {index + 1}<small>/{steps.length}</small></span>
            <div><p>{step?.thought || '…'}</p>{step?.action && <span className={`action-chip ${step.action.kind}`}><ActionIcon kind={step.action.kind} />{step.action.kind === 'keys' ? `Press ${step.action.keys?.map(keyName).join(' + ')}` : step.action.label}</span>}</div>
          </div>
          <div className="replay-controls">
            <button className="icon-button" aria-label="Previous step" onClick={() => { setPlaying(false); setIndex(i => Math.max(0, i - 1)); }} disabled={index === 0}><ChevronLeft size={18} /></button>
            <button className="play-button" aria-label={playing ? 'Pause' : 'Play'} onClick={() => { if (atEnd) setIndex(0); setPlaying(!playing || atEnd); }}>{playing ? <Pause size={16} /> : <Play size={16} />}</button>
            <button className="icon-button" aria-label="Next step" onClick={() => { setPlaying(false); setIndex(i => Math.min(steps.length - 1, i + 1)); }} disabled={atEnd}><ChevronRight size={18} /></button>
            <div className="timeline" role="group" aria-label="Steps">
              <input type="range" min={0} max={Math.max(0, steps.length - 1)} value={index} onChange={e => { setPlaying(false); setIndex(Number(e.target.value)); }} aria-label="Replay position" aria-valuetext={`Step ${index + 1} of ${steps.length}`} />
              <div className="timeline-ticks" aria-hidden="true">{steps.map((s, i) => <span key={i} className={`tick ${s.effects?.blocked ? 'blocked' : s.effects?.jitter ? 'jitter' : s.action?.kind || ''} ${i <= index ? 'past' : ''}`} />)}</div>
            </div>
            <button className="speed-button" aria-label={`Playback speed ${speed}x`} onClick={() => setSpeed(SPEEDS[(SPEEDS.indexOf(speed) + 1) % SPEEDS.length])}>{speed}×</button>
          </div>
        </div>
        <aside className="replay-side">
          <ol className="step-list" ref={listRef}>{steps.map((s, i) => <li key={i} data-step={i}><button className={i === index ? 'current' : ''} onClick={() => { setPlaying(false); setIndex(i); }} aria-current={i === index ? 'step' : undefined}>
            <span className={`step-dot ${s.effects?.blocked ? 'blocked' : s.effects?.jitter ? 'jitter' : ''}`}><ActionIcon kind={s.action?.kind} /></span>
            <span><strong>{s.action?.kind === 'keys' ? `Press ${s.action.keys?.map(keyName).join(' + ')}` : s.action?.label || 'Observe'}</strong><small>{s.thought}</small></span>
          </button></li>)}</ol>
          {run.report && <div className="replay-report"><span className="eyebrow">AGENT’S REPORT</span><p>{run.report.summary}</p>{run.report.answer && <p className="replay-answer"><b>Answer</b> {run.report.answer}</p>}{run.report.obstacles.map((o, i) => <div key={i} className="mini-barrier"><span className={`severity ${o.severity}`}>{o.severity}</span><span>{o.what}</span></div>)}</div>}
        </aside>
      </div>}
    </div>
  </Dialog>;
}

function ClickMarker({ step, w, h }: { step: ReplayStep; w?: number; h?: number }) {
  const x = step.action!.x!, y = step.action!.y!;
  const j = step.effects?.jitter;
  const ax = j && w ? x + j[0] / w : x, ay = j && h ? y + j[1] / h : y;
  const blocked = step.effects?.blocked;
  return <div className="click-layer" aria-hidden="true">
    <span className={`target-ring ${blocked ? 'blocked' : ''}`} style={{ left: `${x * 100}%`, top: `${y * 100}%` }} />
    {j && !blocked && <><svg className="jitter-line" viewBox="0 0 100 100" preserveAspectRatio="none"><line x1={x * 100} y1={y * 100} x2={ax * 100} y2={ay * 100} /></svg><span className="hit-dot" style={{ left: `${ax * 100}%`, top: `${ay * 100}%` }} /></>}
    {!j && !blocked && <span className="hit-dot" style={{ left: `${x * 100}%`, top: `${y * 100}%` }} />}
  </div>;
}
