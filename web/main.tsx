import React, { FormEvent, useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowLeft, ArrowRight, ArrowUpRight, Check, CheckCircle2, ChevronDown, ChevronRight, Globe2, LayoutDashboard, LockKeyhole, LogOut, Play, Plus, Search, Settings2, ShieldCheck, Sparkles, Target, Trash2, TriangleAlert, X } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import './style.css';
import './report.css';
import { api, PERSONAS, terminal, pname, host, date, total, Mark, Status, ErrorNote, Spinner, PersonaIcon, Dialog, ScoreRing } from './shared';
import type { Suggestion, User, Journey, Audit, Site, Setup, Quota, VaultEntry } from './shared';
import { AuditDetail } from './audit-detail';
import { CredentialVault } from './vault';
import { SitesPage, VaultSelect } from './sites';


function Auth({ onLogin }: { onLogin: (user: User) => void }) {
  const [register, setRegister] = useState(false), [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault(); setError(''); setBusy(true);
    const data = Object.fromEntries(new FormData(e.currentTarget));
    try { onLogin(await api<User>(register ? '/auth/register' : '/auth/login', { method: 'POST', body: JSON.stringify(data) })); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <main className="auth-layout">
    <section className="auth-story"><Mark /><div className="auth-story-content"><h1>Great experiences.<br />For <em>every</em> body.</h1><p>Browser agents try your key journeys as five different users and show you where they break.</p><div className="perspective-art" aria-hidden="true"><div className="orbit orbit-one" /><div className="orbit orbit-two" /><div className="orbit-center"><span className="brand-mark"><span /><span /><i /></span></div>{PERSONAS.slice(1).map((p, i) => <div key={p.id} className={`orbit-persona orbit-persona-${i}`}><PersonaIcon id={p.id} /><span>{p.name}</span></div>)}</div></div></section>
    <section className="auth-form-wrap"><div className="auth-form"><h2>{register ? 'Create an account' : 'Sign in'}</h2><form onSubmit={submit}>
      {register && <label>Your name<input name="name" placeholder="Alex Morgan" autoComplete="name" maxLength={80} required /></label>}
      <label>Email address<input name="email" type="email" placeholder="you@company.com" autoComplete="email" required /></label>
      <label>Password<input name="password" type="password" placeholder="At least 8 characters" minLength={8} maxLength={128} autoComplete={register ? 'new-password' : 'current-password'} required /></label>
      {error && <ErrorNote message={error} />}<button className="button primary auth-submit" disabled={busy}>{busy ? <Spinner /> : register ? 'Create account' : 'Sign in'}<ArrowRight size={17} /></button>
    </form><p className="auth-switch">{register ? 'Already have an account?' : 'New to Humble?'} <button className="text-button" onClick={() => { setRegister(!register); setError(''); }}>{register ? 'Sign in' : 'Create an account'}</button></p></div></section>
  </main>;
}

type Draft = { template?: Audit; url?: string; site?: Site };

function App() {
  const [user, setUser] = useState<User | null>(null), [loading, setLoading] = useState(true);
  const [route, setRoute] = useState(location.hash.slice(1) || 'overview');
  const [audits, setAudits] = useState<Audit[]>([]), [sites, setSites] = useState<Site[]>([]), [setup, setSetup] = useState<Setup | null>(null), [listed, setListed] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null), [refreshError, setRefreshError] = useState(''), [query, setQuery] = useState(''), [startUrl, setStartUrl] = useState('');
  useEffect(() => { api<User>('/auth/me').then(setUser).catch(() => {}).finally(() => setLoading(false)); const changed = () => { setRoute(location.hash.slice(1) || 'overview'); setQuery(''); }; window.addEventListener('hashchange', changed); return () => window.removeEventListener('hashchange', changed); }, []);
  const refresh = React.useCallback(async () => {
    try { const [a, p, s] = await Promise.all([api<Audit[]>('/audits'), api<Site[]>('/sites'), api<Setup>('/settings')]); setAudits(a); setSites(p); setSetup(s); setRefreshError(''); setListed(true); }
    catch (e) { setRefreshError((e as Error).message); }
  }, []);
  useEffect(() => { if (user) void refresh(); }, [user, refresh, route]);  // keep the list fresh when coming back home
  useEffect(() => { if (!user || !audits.some(a => !terminal(a.status))) return; const interval = setInterval(() => void refresh(), 5000); return () => clearInterval(interval); }, [audits, user, refresh]);
  const openNew = (d: Draft = {}) => setDraft(d);
  const closeNew = React.useCallback(() => setDraft(null), []);
  const logout = async () => {
    try { await api('/auth/logout', { method: 'POST' }); setUser(null); setAudits([]); setSites([]); }
    catch (e) { setRefreshError((e as Error).message); }
  };
  const go = (path: string) => { location.hash = path; };
  if (loading) return <div className="boot"><Mark /><Spinner /></div>;
  if (!user) return <Auth onLogin={setUser} />;
  const active = audits.find(a => !terminal(a.status));
  const page = route.startsWith('audit/') ? 'audit' : route;
  const filtered = audits.filter(a => `${a.name} ${a.url}`.toLowerCase().includes(query.toLowerCase()));
  const nav: { id: string; icon: LucideIcon; title: string }[] = [{ id: 'overview', icon: LayoutDashboard, title: 'Audits' }, { id: 'sites', icon: Globe2, title: 'Sites' }, { id: 'vault', icon: LockKeyhole, title: 'Credentials' }];
  return <div className="app-shell">
    <a className="skip-link" href="#main-content">Skip to content</a>
    <aside className="sidebar"><a href="#overview" className="brand-link" aria-label="Humble home"><Mark /></a>
      <nav>{nav.map(n => <a key={n.id} href={`#${n.id}`} className={`nav-item ${page === n.id || (n.id === 'overview' && ['history', 'audit'].includes(page)) ? 'active' : ''}`}><n.icon size={18} strokeWidth={1.7} />{n.title}</a>)}</nav>
      <button className="button primary sidebar-new" onClick={() => openNew()}><Plus size={16} />New audit</button>
      {active && <button className="active-run" onClick={() => go(`audit/${active.id}`)}><span className="live-dot" /><span>Audit in progress<small>{active.progress.completed || 0} of {total(active)} runs finished</small></span><ChevronRight size={14} /></button>}
      <div className="sidebar-bottom"><a href="#settings" className={`nav-item ${page === 'settings' ? 'active' : ''}`}><Settings2 size={18} />Settings</a><div className="user-row"><span className="user-avatar">{user.name.slice(0, 1).toUpperCase()}</span><div><strong>{user.name}</strong></div><button className="icon-button" title="Sign out" aria-label="Sign out" onClick={() => void logout()}><LogOut size={16} /></button></div></div>
    </aside>
    <div className="main-shell">
      <main id="main-content" className="main-content" tabIndex={-1}>
        {refreshError && <ErrorNote message={refreshError} />}
        {(page === 'overview' || page === 'history') && <>
          <section className="home-hero"><div className="home-hero-copy"><h1>See your site through <em>five different users.</em></h1><p>H agents run your key journeys in parallel, each in a browser with an impairment applied. You get a score, replays of where they got stuck, and fixes you can re-test.</p></div>
            <form className="home-start" onSubmit={e => { e.preventDefault(); openNew({ url: startUrl, site: sites.find(s => s.url === startUrl) }); }}><label className="sr-only" htmlFor="start-url">Website URL</label><div className="input-icon"><Globe2 size={17} /><input id="start-url" type="url" value={startUrl} onChange={e => setStartUrl(e.target.value)} placeholder="https://your-product.com" required /></div><button className="button primary">Audit this site<ArrowRight size={16} /></button></form>
            <div className="home-personas" aria-hidden="true">{PERSONAS.map(p => <span key={p.id}><PersonaIcon id={p.id} size={14} />{p.name}</span>)}</div>
          </section>
          {!listed && <div className="loading-panel"><Spinner />Loading your audits…</div>}
          {audits.length > 0 && <section className="section"><div className="section-heading"><h2>Audits</h2><SearchBox value={query} onChange={setQuery} placeholder="Search by name or website…" /></div><AuditTable audits={filtered} onOpen={id => go(`audit/${id}`)} onNew={() => openNew()} onDeleted={refresh} searching={!!query} /></section>}
        </>}
        {page === 'sites' && <SitesPage onAudit={site => openNew({ site })} onChange={refresh} />}
        {page === 'vault' && <CredentialVault />}
        {page === 'settings' && <Environment setup={setup} user={user} onLogout={logout} />}
        {page === 'audit' && <AuditDetail id={route.split('/')[1]} onBack={() => go('history')} onRerun={a => openNew({ template: a })} onUpdate={refresh} onOpen={next => go(`audit/${next}`)} />}
      </main>
    </div>
    {draft && <NewAudit close={closeNew} template={draft.template} presetUrl={draft.url} site={draft.site} sites={sites} setup={setup} onCreated={a => { setDraft(null); void refresh(); go(`audit/${a.id}`); }} />}
  </div>;
}

function SearchBox({ value, onChange, placeholder }: { value: string; onChange: (s: string) => void; placeholder: string }) { return <div className="search-box"><Search size={17} /><input aria-label="Search audits" value={value} onChange={e => onChange(e.target.value)} placeholder={placeholder} /></div>; }
function Empty({ icon: Icon, title, text, action, actionText }: { icon: LucideIcon; title: string; text: string; action?: () => void; actionText?: string }) { return <div className="empty-state"><span className="empty-icon"><Icon size={25} strokeWidth={1.4} /></span><h3>{title}</h3><p>{text}</p>{action && <button className="button secondary" onClick={action}>{actionText}<ArrowRight size={15} /></button>}</div>; }

function AuditTable({ audits, onOpen, onNew, onDeleted, searching = false }: { audits: Audit[]; onOpen: (id: string) => void; onNew: () => void; onDeleted: () => Promise<void>; searching?: boolean }) {
  const [removing, setRemoving] = useState<Audit | null>(null), [busy, setBusy] = useState(false), [error, setError] = useState('');
  const closeRemove = React.useCallback(() => { if (!busy) setRemoving(null); }, [busy]);
  async function remove() {
    if (!removing || busy) return;
    setBusy(true); setError('');
    try { await api(`/audits/${removing.id}`, { method: 'DELETE' }); setRemoving(null); await onDeleted(); }
    catch (err) { setError((err as Error).message); }
    finally { setBusy(false); }
  }
  if (!audits.length) return <Empty icon={searching ? Search : Target} title={searching ? 'No matching audits.' : 'No audit yet.'} text={searching ? 'Try another name or website.' : 'Choose a website and a journey to start.'} action={searching ? undefined : onNew} actionText="Start an audit" />;
  return <><div className="table-wrap"><table className="audit-table"><thead><tr><th>Audit</th><th>Score</th><th>Perspectives</th><th>Status</th><th>Created</th><th><span className="sr-only">Audit actions</span></th></tr></thead><tbody>{audits.map(a => {
    const per = a.scores?.perspectives || {};
    return <tr key={a.id}><td><button className="audit-name" onClick={() => onOpen(a.id)}><span className="site-icon small"><Globe2 size={18} /></span><span><strong>{a.name}</strong><small>{host(a.url)}{a.parent_id ? ' · re-test with fixes' : a.config.mode === 'auto' ? ' · fix until it passes' : ''}</small></span></button></td>
      <td>{a.scores?.score != null ? <span className="table-score"><ScoreRing score={a.scores.score} grade={a.scores.grade} size={38} stroke={4} /><b className={`grade-text grade-${a.scores.grade}`}>{a.scores.grade}</b></span> : <span className="muted-dash">—</span>}</td>
      <td><div className="persona-outcomes-row">{a.config.personas.map(id => { const p = per[id]; const state = !p || p.completion == null ? 'pending' : p.completion === 1 ? 'pass' : p.completion ? 'partial' : 'fail'; return <span key={id} className={`persona-outcome ${state}`} title={`${pname(id)}: ${state === 'pending' ? 'not scored' : state === 'pass' ? 'completed' : state === 'partial' ? 'partly completed' : 'blocked'}`}><PersonaIcon id={id} size={13} /></span>; })}</div></td>
      <td><Status value={a.status} /></td><td className="table-date">{date(a.created)}</td><td><button className="icon-button" onClick={() => onOpen(a.id)} aria-label={`Open ${a.name}`}><ArrowUpRight size={17} /></button><button className="icon-button" disabled={!terminal(a.status)} title={terminal(a.status) ? 'Delete audit' : 'Stop this audit before deleting it'} aria-label={`Delete ${a.name}`} onClick={() => { setError(''); setRemoving(a); }}><Trash2 size={16} /></button></td></tr>;
  })}</tbody></table></div>
    {removing && <Dialog close={closeRemove} title="Delete audit"><div className="confirm-dialog"><span className="empty-icon"><Trash2 size={22} /></span><h2>Delete “{removing.name}”?</h2><p>This permanently deletes the report, screenshots and replays. Re-evaluations are kept.</p>{error && <ErrorNote message={error} />}<div className="confirm-actions"><button className="button secondary" disabled={busy} onClick={closeRemove}>Cancel</button><button className="button danger" disabled={busy} onClick={() => void remove()}>{busy ? <Spinner /> : <Trash2 size={15} />}{busy ? 'Deleting…' : 'Delete audit'}</button></div></div></Dialog>}
  </>;
}

const blank = (): Journey => ({ title: '', goal: '', expect_answer_contains: '' });
const sameJourney = (a: Journey, b: Journey) => (!!a.id && a.id === b.id) || (a.title.trim() === b.title.trim() && a.goal.trim() === b.goal.trim());
const isBlank = (t: Journey) => !t.title.trim() && !t.goal.trim();

function NewAudit({ close, template, presetUrl = '', site, sites: listed, setup, onCreated }: { close: () => void; template?: Audit; presetUrl?: string; site?: Site; sites: Site[]; setup: Setup | null; onCreated: (a: Audit) => void }) {
  const sites = site && !listed.some(s => s.id === site.id) ? [site, ...listed] : listed;
  const [step, setStep] = useState(0), [name, setName] = useState(template?.name || ''), [url, setUrl] = useState(template?.url || site?.url || presetUrl);
  const [suggest, setSuggest] = useState<Suggestion | null>(null), [suggesting, setSuggesting] = useState(false), [suggestError, setSuggestError] = useState('');
  const [auth, setAuth] = useState(false), [username, setUsername] = useState(''), [password, setPassword] = useState(''), [headers, setHeaders] = useState('');
  const startVault = template?.config.vault_id || site?.vault_id || '';
  const [useVault, setUseVault] = useState(!!startVault), [vaultId, setVaultId] = useState(startVault);
  const [vaults, setVaults] = useState<VaultEntry[]>([]), [vaultError, setVaultError] = useState('');
  useEffect(() => { api<VaultEntry[]>('/vault').then(setVaults).catch(e => setVaultError(e.message)); }, []);
  let siteOrigin = ''; try { siteOrigin = new URL(url).origin; } catch { /* URL field validates before continuing */ }
  const matched = sites.find(s => s.url === url) || (siteOrigin ? sites.find(s => s.origin === siteOrigin) : undefined);
  const selectedVault = vaults.find(v => v.id === vaultId);
  const [tasks, setTasks] = useState<Journey[]>(template?.config.tasks.map(t => ({ title: t.title, goal: t.goal, expect_answer_contains: t.expect_answer_contains || '' })) || (site?.journeys.length ? site.journeys.slice(0, 5) : [blank()]));
  const [saveSite, setSaveSite] = useState<boolean | null>(null);
  const save = saveSite ?? !!matched;
  const [personas, setPersonas] = useState(template?.config.personas || PERSONAS.map(p => p.id));
  const [repetitions, setRepetitions] = useState(template?.config.repetitions || 1), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  const [mode, setMode] = useState<'standard' | 'auto'>(template?.config.mode || 'standard'), [maxRounds, setMaxRounds] = useState(template?.config.max_rounds || 2);
  const [quota, setQuota] = useState<Quota | null>(null), [quotaError, setQuotaError] = useState('');
  useEffect(() => { if (step === 2) { setQuotaError(''); api<Quota>('/quota').then(setQuota).catch(e => setQuotaError(e.message)); } }, [step]);
  let publicSite = false; try { const h = new URL(url).hostname; publicSite = !!h && h !== 'localhost' && !/^(127\.|10\.|192\.168\.|0\.)/.test(h) && !/\.(test|local|localhost)$/.test(h) && h.includes('.'); } catch { /* invalid URL */ }
  function pickSite(s: Site) {
    setUrl(s.url);
    if (s.vault_id && vaults.some(v => v.id === s.vault_id)) { setUseVault(true); setVaultId(s.vault_id); }
    if (s.journeys.length && tasks.every(isBlank)) setTasks(s.journeys.slice(0, 5));
  }
  function togglePreset(p: Journey, on: boolean) {
    if (on) { const left = tasks.filter(t => !sameJourney(t, p)); setTasks(left.length ? left : [blank()]); }
    else setTasks([...tasks.filter(t => !isBlank(t)), p].slice(0, 5));
  }
  async function suggestJourneys() {
    setSuggesting(true); setSuggestError('');
    try {
      const r = await api<Suggestion>('/research/journeys', { method: 'POST', body: JSON.stringify({ url }) });
      setSuggest(r);
      const kept = tasks.filter(t => !isBlank(t));
      setTasks([...kept, ...r.journeys.map(j => ({ title: j.title.slice(0, 100), goal: j.goal.slice(0, 2000), expect_answer_contains: '' }))].slice(0, Math.max(kept.length, 1) + 2).slice(0, 5));
    } catch (e) { setSuggestError((e as Error).message); } finally { setSuggesting(false); }
  }
  const updateTask = (i: number, field: keyof Journey, value: string) => setTasks(t => t.map((task, index) => index === i ? { ...task, [field]: value, ...(field !== 'expect_answer_contains' ? { id: undefined } : {}) } : task));
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    if (useVault && !selectedVault) { setError('Choose saved credentials, or enter them for this audit.'); return; }
    if (step === 0 && matched?.journeys.length && tasks.every(isBlank)) setTasks(matched.journeys.slice(0, 5));  // its saved journeys
    if (step < 2) { setStep(step + 1); return; }
    let parsedHeaders: Record<string, string> = {};
    if (!useVault && headers.trim()) {
      try { parsedHeaders = JSON.parse(headers); if (!parsedHeaders || typeof parsedHeaders !== 'object' || Array.isArray(parsedHeaders) || Object.values(parsedHeaders).some(v => typeof v !== 'string')) throw new Error(); }
      catch { setError('Access headers must be a JSON object with text values.'); return; }
    }
    setBusy(true);
    const auditName = name.trim() || (matched ? `${matched.name} · ${tasks[0].title}` : host(url));
    try {
      const a = await api<Audit>('/audits', { method: 'POST', body: JSON.stringify({ name: auditName.slice(0, 100), url, tasks: tasks.map(t => ({ title: t.title, goal: t.goal, expect_answer_contains: t.expect_answer_contains || null })), personas, repetitions, mode, max_rounds: maxRounds, save_site: save, vault_id: useVault ? vaultId : null, credentials: useVault ? {} : { username: auth ? username : '', password: auth ? password : '', headers: parsedHeaders } }) });
      setPassword(''); setUsername(''); setHeaders(''); onCreated(a);
    } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  }
  const runs = tasks.length * personas.length * repetitions;
  return <Dialog close={close} title="New audit"><div className="modal-top"><h2>New audit</h2><button className="icon-button" onClick={close} aria-label="Close"><X size={20} /></button></div><div className="wizard-steps">{['Website', 'Journeys', 'Perspectives'].map((label, i) => <div key={label} className={`${i === step ? 'current' : ''} ${i < step ? 'done' : ''}`}><span>{i < step ? <Check size={13} /> : i + 1}</span>{label}</div>)}</div>
    <form onSubmit={submit}><div className="wizard-content" key={step}>
      {step === 0 && <>
        {sites.length > 0 && <div className="site-picker" role="group" aria-label="Saved sites"><span>Saved sites</span>{sites.map(s => <button type="button" key={s.id} aria-pressed={matched?.id === s.id} className={`site-chip ${matched?.id === s.id ? 'on' : ''}`} onClick={() => pickSite(s)}><Globe2 size={13} />{s.name}</button>)}</div>}
        <label>Website URL<div className="input-icon"><Globe2 size={17} /><input type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="https://your-product.com" required autoFocus={!url} /></div></label>
        <label>Audit name<input value={name} onChange={e => setName(e.target.value)} placeholder={`Optional · defaults to ${matched ? `${matched.name} · journey name` : 'the website name'}`} maxLength={100} /></label>
        <div className="access-choice" aria-label="Credential source"><button type="button" aria-pressed={!useVault} className={!useVault ? 'selected' : ''} onClick={() => setUseVault(false)}>Enter for this audit</button><button type="button" aria-pressed={useVault} className={useVault ? 'selected' : ''} onClick={() => setUseVault(true)}><LockKeyhole size={14} />From the vault</button></div>
        {useVault ? <div className="vault-link-panel"><label>Saved credentials<VaultSelect entries={vaults} url={url} value={vaultId} onChange={setVaultId} /></label>
          {selectedVault && siteOrigin && selectedVault.origin !== siteOrigin && <p className="field-hint">Saved for {selectedVault.origin.replace(/^https?:\/\//, '')}. It will be used on {host(url)}.</p>}
          {!vaults.length && <p className="field-hint">No saved credentials yet. <a href="#vault" onClick={close}>Add some in the vault</a>.</p>}{vaultError && <ErrorNote message={vaultError} />}</div>
          : <><div className="auth-toggle"><div><LockKeyhole size={18} /><span><strong>This website requires a login</strong><small>Use a test account.</small></span></div><button type="button" role="switch" aria-checked={auth} aria-label="Website requires a login" className={`switch ${auth ? 'on' : ''}`} onClick={() => setAuth(!auth)}><span /></button></div>{auth && <div className="field-grid"><label>Test account email / username<input value={username} onChange={e => setUsername(e.target.value)} placeholder="test@example.com" autoComplete="off" required maxLength={254} /></label><label>Test account password<input type="password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Test account password" autoComplete="new-password" required maxLength={1024} /></label></div>}<details className="advanced"><summary>Access headers <ChevronDown size={14} /></summary><label>HTTP headers (JSON)<textarea value={headers} onChange={e => setHeaders(e.target.value)} placeholder={'{"X-Audit-Token": "your-staging-token"}'} rows={3} spellCheck={false} /></label><p>Applied to browser requests, never saved with the audit.</p></details></>}
      </>}
      {step === 1 && <>
        {matched && matched.journeys.length > 0 && <div className="preset-list" role="group" aria-label={`Saved journeys for ${matched.name}`}><span>Saved journeys</span>{matched.journeys.map(p => { const on = tasks.some(t => sameJourney(t, p)); return <button type="button" key={p.id} aria-pressed={on} className={`preset-chip ${on ? 'on' : ''}`} onClick={() => togglePreset(p, on)}>{on ? <Check size={13} /> : <Plus size={13} />}{p.title}</button>; })}</div>}
        {publicSite && <div className="linkup-suggest"><div><Sparkles size={16} /><span><strong>Not sure what to test?</strong><small>Linkup searches the web for what visitors come to do on {host(url)}.</small></span></div><button type="button" className="button secondary" disabled={suggesting || !setup?.linkup_configured} onClick={() => void suggestJourneys()}>{suggesting ? <Spinner /> : <Search size={14} />}{suggesting ? 'Searching…' : 'Suggest journeys'}</button></div>}
        {suggest && <div className="linkup-result"><p><b>{suggest.site_name}.</b> {suggest.what_it_is}</p>{suggest.declared_compliance && <p><ShieldCheck size={13} />Declared accessibility: {suggest.declared_compliance}{suggest.accessibility_statement_url && <> · <a href={suggest.accessibility_statement_url} target="_blank" rel="noreferrer">statement<ArrowUpRight size={11} /></a></>}</p>}</div>}
        {suggestError && <ErrorNote message={suggestError} />}
        {tasks.map((t, i) => <div className="journey-form" key={i}><div className="journey-form-heading"><strong>Journey {i + 1}</strong>{tasks.length > 1 && <button type="button" className="icon-button" aria-label={`Remove journey ${i + 1}`} onClick={() => setTasks(tasks.filter((_, index) => index !== i))}><Trash2 size={15} /></button>}</div><label>Name<input value={t.title} onChange={e => updateTask(i, 'title', e.target.value)} placeholder="Find a product and reach checkout" required maxLength={100} /></label><label>User goal<textarea value={t.goal} onChange={e => updateTask(i, 'goal', e.target.value)} placeholder="Find an in-stock blue mug, add it to the cart and report the total shown at checkout. Stop before paying." rows={3} required minLength={10} maxLength={2000} /><small>End before payment, sending messages or deleting data.</small></label><label>Expected answer<input value={t.expect_answer_contains || ''} onChange={e => updateTask(i, 'expect_answer_contains', e.target.value)} placeholder="Optional · e.g. 98.00; 8 Dec" maxLength={200} /><small>Facts the answer must contain, separated by “;”. Any format is accepted: Holo judges the answer.</small></label></div>)}
        {tasks.length < 5 && <button type="button" className="button dashed" onClick={() => setTasks([...tasks, blank()])}><Plus size={16} />Add a journey</button>}
        <label className="check-line"><input type="checkbox" checked={save} onChange={e => setSaveSite(e.target.checked)} />{matched ? `Save new journeys to ${matched.name}` : `Save ${host(url) || 'this site'} and these journeys to Sites`}</label>
      </>}
      {step === 2 && <>
        <div className="persona-picker">{PERSONAS.map(p => <label key={p.id} className={`persona-choice ${personas.includes(p.id) ? 'selected' : ''}`}><input type="checkbox" checked={personas.includes(p.id)} disabled={p.id === 'baseline'} onChange={e => setPersonas(e.target.checked ? [...personas, p.id] : personas.filter(id => id !== p.id))} /><PersonaIcon id={p.id} /><span><strong>{p.name}{p.id === 'baseline' && <span className="tiny-tag">ALWAYS</span>}</strong><small>{p.description}</small></span><span className="checkbox-display">{personas.includes(p.id) && <Check size={12} />}</span></label>)}</div>
        <div className="repeat-control"><strong>Runs per perspective</strong><select aria-label="Runs per perspective" value={repetitions} onChange={e => setRepetitions(Number(e.target.value))}><option value={1}>1 run</option><option value={3}>3 runs</option><option value={5}>5 runs</option></select></div>
        <div className="fix-mode"><div><strong>After the audit</strong><p>{mode === 'standard' ? 'Suggested fixes with before/after images. You choose when to re-test with them.' : 'Apply the fixes and audit again, until every perspective passes.'}</p></div><div className="access-choice" aria-label="After the audit"><button type="button" aria-pressed={mode === 'standard'} className={mode === 'standard' ? 'selected' : ''} onClick={() => setMode('standard')}>Suggest fixes</button><button type="button" aria-pressed={mode === 'auto'} className={mode === 'auto' ? 'selected' : ''} onClick={() => setMode('auto')}><Sparkles size={14} />Fix until it passes</button></div>{mode === 'auto' && <select aria-label="Maximum fix rounds" value={maxRounds} onChange={e => setMaxRounds(Number(e.target.value))}><option value={1}>At most 1 fix round</option><option value={2}>At most 2 fix rounds</option><option value={3}>At most 3 fix rounds</option></select>}</div>
        <div className="audit-recap"><span>{tasks.length} journey{tasks.length > 1 ? 's' : ''} × {personas.length} perspectives{repetitions > 1 ? ` × ${repetitions} runs` : ''}{useVault && selectedVault ? ` · ${selectedVault.name}` : ''}</span><strong>{runs} run{runs > 1 ? 's' : ''}</strong></div>
        {quota && <div className={`quota-note ${quota.available < 1 ? 'unavailable' : ''}`}><span className="live-dot" />{quota.available} of {quota.limit} H slots free{quota.available < 2 ? ' · agents start as H frees slots' : ''}</div>}{quotaError && <ErrorNote message={quotaError} />}{setup && (!setup.h_configured || !setup.chrome_ready) && <ErrorNote message={!setup.h_configured ? 'Add HAI_API_KEY to .env and restart the server before launching.' : 'Install Google Chrome or set AUDIT_CHROME in .env, then restart the server.'} />}</>}
      {error && <ErrorNote message={error} />}
    </div><div className="modal-footer"><button type="button" className="button ghost" onClick={() => step ? setStep(step - 1) : close()} disabled={busy}>{step ? <ArrowLeft size={15} /> : null}{step ? 'Back' : 'Cancel'}</button><button className="button primary" disabled={busy || step === 2 && (!!quota && quota.available < 1 || !!setup && (!setup.h_configured || !setup.chrome_ready))}>{busy ? <Spinner /> : step === 2 ? <Play size={15} /> : null}{busy ? 'Starting…' : step === 2 ? 'Launch audit' : 'Continue'}{step < 2 && <ArrowRight size={15} />}</button></div></form>
  </Dialog>;
}

function Line({ label, ok, text }: { label: string; ok?: boolean; text: string }) { return <div className="setting-line"><span>{label}</span><span className={`connection-state ${ok ? 'ok' : ''}`}>{ok ? <CheckCircle2 size={15} /> : <TriangleAlert size={15} />}{text}</span></div>; }

function Environment({ setup, user, onLogout }: { setup: Setup | null; user: User; onLogout: () => Promise<void> }) {
  const [quota, setQuota] = useState<Quota | null>(null), [error, setError] = useState(''), [busy, setBusy] = useState(false);
  async function check() { setBusy(true); setError(''); try { setQuota(await api<Quota>('/quota')); } catch (e) { setError((e as Error).message); } finally { setBusy(false); } }
  return <><div className="page-heading"><div><h1>Settings</h1></div></div><div className="settings-grid">
    <section className="settings-card"><h2>Connections</h2>
      <Line label="H API key (agents + Holo judge)" ok={setup?.h_configured} text={setup?.h_configured ? 'Configured' : 'Missing'} />
      <Line label="Chrome" ok={setup?.chrome_ready} text={setup?.chrome_ready ? 'Detected' : 'Not found'} />
      <Line label="Linkup (journey suggestions)" ok={setup?.linkup_configured} text={setup?.linkup_configured ? 'Configured' : 'Missing LINKUP_API_KEY'} />
      <Line label="Devin (fixes)" ok={setup?.devin_configured} text={setup?.devin_configured ? 'Configured' : 'Rule engine fallback'} />
      <p className="settings-help">Keys live in the root .env file. Restart the server after a change.</p>
      <button className="button secondary" disabled={busy} onClick={() => void check()}>{busy ? <Spinner /> : <ShieldCheck size={16} />}Check H quota</button>{quota && <div className="quota-result"><strong>{quota.available} / {quota.limit}</strong><span>slots free · each agent needs 2</span></div>}{error && <ErrorNote message={error} />}</section>
    <section className="settings-card"><h2>Account</h2><div className="setting-line"><span>Name</span><strong>{user.name}</strong></div><div className="setting-line"><span>Email</span><strong>{user.email}</strong></div><button className="button secondary settings-signout" onClick={() => void onLogout()}><LogOut size={15} />Sign out</button></section>
  </div></>;
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>);
