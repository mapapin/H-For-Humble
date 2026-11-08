import React, { FormEvent, useEffect, useState } from 'react';
import { Globe2, LockKeyhole, Pencil, Play, Plus, Search, Sparkles, Trash2, X } from 'lucide-react';
import { api, date, Dialog, ErrorNote, host, ScoreRing, Spinner } from './shared';
import type { Journey, Site, Setup, Suggestion, VaultEntry } from './shared';

/** Vault entries for a website: the ones saved for its origin first, then every other one. */
export function rankVault(entries: VaultEntry[], url: string) {
  let origin = ''; try { origin = new URL(url).origin; } catch { /* no URL yet */ }
  return { origin, mine: origin ? entries.filter(v => v.origin === origin) : [], others: origin ? entries.filter(v => v.origin !== origin) : entries };
}

export function VaultSelect({ entries, url, value, onChange, emptyLabel = 'Choose saved credentials' }: { entries: VaultEntry[]; url: string; value: string; onChange: (id: string) => void; emptyLabel?: string }) {
  const { mine, others } = rankVault(entries, url);
  const option = (v: VaultEntry) => <option key={v.id} value={v.id}>{v.name}{mine.includes(v) ? '' : ` · ${v.origin.replace(/^https?:\/\//, '')}`}</option>;
  return <select value={value} onChange={e => onChange(e.target.value)}>
    <option value="">{emptyLabel}</option>
    {mine.length > 0 && <optgroup label="Saved for this website">{mine.map(option)}</optgroup>}
    {others.length > 0 && (mine.length ? <optgroup label="Other websites">{others.map(option)}</optgroup> : others.map(option))}
  </select>;
}

export function SitesPage({ onAudit, onChange }: { onAudit: (site: Site) => void; onChange?: () => void }) {
  const [sites, setSites] = useState<Site[]>([]), [vaults, setVaults] = useState<VaultEntry[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [editing, setEditing] = useState<Site | 'new' | null>(null), [removing, setRemoving] = useState<Site | null>(null), [busy, setBusy] = useState(false);
  const refresh = React.useCallback(async () => {
    try { const [s, v] = await Promise.all([api<Site[]>('/sites'), api<VaultEntry[]>('/vault')]); setSites(s); setVaults(v); setError(''); onChange?.(); }
    catch (e) { setError((e as Error).message); } finally { setLoading(false); }
  }, [onChange]);
  useEffect(() => { void refresh(); }, [refresh]);
  const closeEditor = React.useCallback(() => setEditing(null), []), closeRemove = React.useCallback(() => setRemoving(null), []);
  async function remove() {
    if (!removing) return;
    setBusy(true);
    try { await api(`/sites/${removing.id}`, { method: 'DELETE' }); setRemoving(null); await refresh(); }
    catch (e) { setError((e as Error).message); setRemoving(null); } finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><h1>Sites</h1><p>Websites you test often, with their journeys and default credentials.</p></div><button className="button primary" onClick={() => setEditing('new')}><Plus size={16} />Add a site</button></div>
    {error && <ErrorNote message={error} />}
    {loading ? <div className="loading-panel"><Spinner />Loading sites…</div> : sites.length ? <div className="site-list">{sites.map(site => {
      const cred = vaults.find(v => v.id === site.vault_id);
      return <article className="site-row" key={site.id}>
        <span className="site-icon"><Globe2 size={20} /></span>
        <div className="site-main"><h2>{site.name}</h2><p>{site.url}</p>
          <div className="site-meta">{site.journeys.length ? site.journeys.map(j => <span key={j.id} className="site-journey">{j.title}</span>) : <span className="muted">No saved journey</span>}{cred && <span className="site-cred"><LockKeyhole size={12} />{cred.name}</span>}</div></div>
        <div className="site-score">{site.last_score != null ? <ScoreRing score={site.last_score} grade={site.last_grade ?? null} size={40} stroke={4} /> : null}<small>{site.audit_count ? `${site.audit_count} audit${site.audit_count > 1 ? 's' : ''}${site.last_audit ? ` · ${date(site.last_audit)}` : ''}` : 'Not audited yet'}</small></div>
        <div className="site-actions"><button className="button primary" onClick={() => onAudit(site)}><Play size={14} />Audit</button><button className="icon-button" aria-label={`Edit ${site.name}`} title="Edit" onClick={() => setEditing(site)}><Pencil size={15} /></button><button className="icon-button" aria-label={`Remove ${site.name}`} title="Remove" onClick={() => setRemoving(site)}><Trash2 size={15} /></button></div>
      </article>;
    })}</div> : <div className="empty-state"><span className="empty-icon"><Globe2 size={24} /></span><h3>No saved site yet.</h3><p>Save a website with its journeys to launch an audit in two clicks. You can also save one when you launch an audit.</p><button className="button secondary" onClick={() => setEditing('new')}><Plus size={15} />Add a site</button></div>}
    {editing && <SiteEditor site={editing === 'new' ? undefined : editing} vaults={vaults} close={closeEditor} saved={() => { setEditing(null); void refresh(); }} />}
    {removing && <Dialog close={closeRemove} title="Remove saved site"><div className="confirm-dialog"><span className="empty-icon"><Trash2 size={22} /></span><h2>Remove “{removing.name}”?</h2><p>Its saved journeys are removed. Past audits of this site stay in your history.</p><div className="confirm-actions"><button className="button secondary" onClick={closeRemove} disabled={busy}>Cancel</button><button className="button danger" onClick={() => void remove()} disabled={busy}>{busy ? <Spinner /> : <Trash2 size={15} />}Remove</button></div></div></Dialog>}
  </>;
}

const blank = (): Journey => ({ title: '', goal: '', expect_answer_contains: '' });

function SiteEditor({ site, vaults, close, saved }: { site?: Site; vaults: VaultEntry[]; close: () => void; saved: () => void }) {
  const [name, setName] = useState(site?.name || ''), [url, setUrl] = useState(site?.url || ''), [vaultId, setVaultId] = useState(site?.vault_id || '');
  const [journeys, setJourneys] = useState<Journey[]>(site?.journeys.length ? site.journeys : [blank()]);
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  const [setup, setSetup] = useState<Setup | null>(null), [suggesting, setSuggesting] = useState(false);
  const [suggestError, setSuggestError] = useState(''), [suggest, setSuggest] = useState<Suggestion | null>(null);
  const currentUrl = React.useRef(url);
  currentUrl.current = url;
  useEffect(() => { api<Setup>('/settings').then(setSetup).catch(() => {}); }, []);
  let publicSite = false;
  try {
    const parsed = new URL(url), h = parsed.hostname.toLowerCase();
    publicSite = ['http:', 'https:'].includes(parsed.protocol) && h.includes('.') &&
      !/^(127\.|10\.|192\.168\.|0\.|169\.254\.|172\.(1[6-9]|2\d|3[01])\.)/.test(h) &&
      !/\.(test|local|localhost|internal)$/.test(h);
  } catch { /* invalid URL */ }
  async function suggestJourneys() {
    const requestedUrl = url;
    setSuggesting(true); setSuggestError('');
    try {
      const result = await api<Suggestion>('/research/journeys', { method: 'POST', body: JSON.stringify({ url: requestedUrl }) });
      if (currentUrl.current !== requestedUrl) return;
      setSuggest(result);
      setJourneys(existing => [...existing.filter(j => j.title.trim() || j.goal.trim()),
        ...result.journeys.map(j => ({ title: j.title.slice(0, 100), goal: j.goal.slice(0, 2000), expect_answer_contains: '' }))].slice(0, 20));
    } catch (err) { if (currentUrl.current === requestedUrl) setSuggestError((err as Error).message); }
    finally { setSuggesting(false); }
  }
  const update = (i: number, field: keyof Journey, value: string) => setJourneys(js => js.map((j, k) => k === i ? { ...j, [field]: value } : j));
  async function submit(e: FormEvent) {
    e.preventDefault(); setError(''); setBusy(true);
    const kept = journeys.filter(j => j.title.trim() || j.goal.trim()).map(j => ({ id: j.id, title: j.title.trim(), goal: j.goal.trim(), expect_answer_contains: j.expect_answer_contains?.trim() || null }));
    try { await api(site ? `/sites/${site.id}` : '/sites', { method: site ? 'PUT' : 'POST', body: JSON.stringify({ name, url, journeys: kept, vault_id: vaultId || null }) }); saved(); }
    catch (err) { setError((err as Error).message); } finally { setBusy(false); }
  }
  return <Dialog close={close} title={site ? 'Edit site' : 'Add a site'}><div className="modal-top"><h2>{site ? 'Edit site' : 'Add a site'}</h2><button className="icon-button" aria-label="Close" onClick={close}><X size={20} /></button></div>
    <form onSubmit={submit}><div className="wizard-content">
      <div className="field-grid"><label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Lune" maxLength={100} required autoFocus /></label>
        <label>URL<input type="url" value={url} onChange={e => { setUrl(e.target.value); setSuggest(null); setSuggestError(''); }} placeholder="https://your-product.com" required /></label></div>
      <label>Default credentials<VaultSelect entries={vaults} url={url} value={vaultId} onChange={setVaultId} emptyLabel="None" /></label>
      <h3 className="form-subhead">Journeys</h3>
      {publicSite && <div className="linkup-suggest"><div><Sparkles size={16} /><span><strong>Not sure what to test?</strong><small>Linkup searches the web for what visitors come to do on {host(url)}.</small></span></div><button type="button" className="button secondary" disabled={busy || suggesting || !setup?.linkup_configured || journeys.length >= 20} onClick={() => void suggestJourneys()}>{suggesting ? <Spinner /> : <Search size={14} />}{suggesting ? 'Searching…' : 'Suggest journeys'}</button></div>}
      {publicSite && suggest && <div className="linkup-result"><p><b>{suggest.site_name}.</b> {suggest.what_it_is}</p></div>}
      {publicSite && suggestError && <ErrorNote message={suggestError} />}
      {journeys.map((j, i) => <div className="journey-form" key={j.id || i}><div className="journey-form-heading"><strong>Journey {i + 1}</strong><button type="button" className="icon-button" aria-label={`Remove journey ${i + 1}`} onClick={() => setJourneys(journeys.filter((_, k) => k !== i))}><Trash2 size={15} /></button></div>
        <label>Name<input value={j.title} onChange={e => update(i, 'title', e.target.value)} placeholder="Book a workshop" maxLength={100} required /></label>
        <label>User goal<textarea value={j.goal} onChange={e => update(i, 'goal', e.target.value)} rows={2} minLength={10} maxLength={2000} required placeholder="Book 2 seats for the first December date and report the total at checkout. Do not pay." /></label>
        <label>Expected answer<input value={j.expect_answer_contains || ''} onChange={e => update(i, 'expect_answer_contains', e.target.value)} placeholder="Optional · e.g. 98.00; 8 Dec" maxLength={200} /></label></div>)}
      {journeys.length < 20 && <button type="button" className="button dashed" onClick={() => setJourneys([...journeys, blank()])}><Plus size={16} />Add a journey</button>}
      {error && <ErrorNote message={error} />}
    </div><div className="modal-footer"><button type="button" className="button ghost" onClick={close}>Cancel</button><button className="button primary" disabled={busy}>{busy ? <Spinner /> : null}Save site</button></div></form>
  </Dialog>;
}
