import React, { FormEvent, useEffect, useState } from 'react';
import { ArrowUpRight, Check, Globe2, LockKeyhole, Plus, RefreshCw, ShieldCheck, Trash2, X } from 'lucide-react';
import { api, date, Dialog, ErrorNote, Spinner } from './shared';
import type { VaultEntry } from './shared';

export function CredentialVault() {
  const [entries, setEntries] = useState<VaultEntry[]>([]);
  const [loading, setLoading] = useState(true), [error, setError] = useState('');
  const [editing, setEditing] = useState<VaultEntry | 'new' | null>(null);
  const [removing, setRemoving] = useState<VaultEntry | null>(null), [busy, setBusy] = useState(false);
  const closeEditor = React.useCallback(() => setEditing(null), []);
  const closeRemove = React.useCallback(() => setRemoving(null), []);
  const refresh = React.useCallback(async () => {
    try { setEntries(await api<VaultEntry[]>('/vault')); setError(''); }
    catch (e) { setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);
  useEffect(() => { void refresh(); }, [refresh]);
  async function remove() {
    if (!removing) return;
    setBusy(true);
    try { await api(`/vault/${removing.id}`, { method: 'DELETE' }); setRemoving(null); await refresh(); }
    catch (e) { setError((e as Error).message); setRemoving(null); }
    finally { setBusy(false); }
  }
  return <>
    <div className="page-heading"><div><h1>Credentials</h1><p>Test accounts and access headers, encrypted on this machine. Pick one when you launch an audit.</p></div><button className="button primary" onClick={() => setEditing('new')}><Plus size={16} />Add credentials</button></div>
    {error && <ErrorNote message={error} />}
    {loading ? <div className="loading-panel"><Spinner />Opening your vault…</div> : entries.length ? <div className="vault-grid">{entries.map(entry => <article className="vault-card" key={entry.id}>
      <div className="vault-card-heading"><span className="site-icon"><LockKeyhole size={21} /></span></div>
      <h2>{entry.name}</h2><p className="vault-origin"><Globe2 size={13} />{entry.origin}</p>
      <div className="vault-badges">{entry.has_login && <span>Account login</span>}{entry.header_count > 0 && <span>{entry.header_count} access header{entry.header_count > 1 ? 's' : ''}</span>}</div>
      <div className="vault-card-footer"><span>Updated {date(entry.updated)}</span><div><button className="icon-button" aria-label={`Replace ${entry.name}`} title="Replace credentials" onClick={() => setEditing(entry)}><RefreshCw size={15} /></button><button className="icon-button" aria-label={`Delete ${entry.name}`} title="Delete entry" onClick={() => setRemoving(entry)}><Trash2 size={15} /></button></div></div>
    </article>)}</div> : <div className="empty-state"><span className="empty-icon"><LockKeyhole size={24} /></span><h3>No saved credentials yet.</h3><p>Add a test login or access headers for a website.</p><button className="button secondary" onClick={() => setEditing('new')}><Plus size={15} />Add your first credentials</button></div>}

    {editing && <VaultEditor entry={editing === 'new' ? undefined : editing} close={closeEditor} saved={() => { setEditing(null); void refresh(); }} />}
    {removing && <Dialog close={closeRemove} title="Delete saved credentials"><div className="confirm-dialog"><span className="empty-icon"><Trash2 size={22} /></span><h2>Remove this vault entry?</h2><p>“{removing.name}” will no longer be available for new audits. Past reports and audits already running keep their results.</p><div className="confirm-actions"><button className="button secondary" onClick={closeRemove}>Keep entry</button><button className="button danger" onClick={() => void remove()} disabled={busy}>{busy ? <Spinner /> : <Trash2 size={14} />}Delete credentials</button></div></div></Dialog>}
  </>;
}

function VaultEditor({ entry, close, saved }: { entry?: VaultEntry; close: () => void; saved: () => void }) {
  const [name, setName] = useState(entry?.name || ''), [url, setUrl] = useState(entry?.origin || '');
  const [username, setUsername] = useState(''), [password, setPassword] = useState(''), [headers, setHeaders] = useState('');
  const [busy, setBusy] = useState(false), [error, setError] = useState('');
  async function submit(e: FormEvent) {
    e.preventDefault(); setError('');
    let values: Record<string, string> = {};
    try {
      if (headers.trim()) { values = JSON.parse(headers); if (!values || typeof values !== 'object' || Array.isArray(values) || Object.values(values).some(v => typeof v !== 'string')) throw new Error(); }
    } catch { setError('Access headers must be a JSON object with text values.'); return; }
    if (!!username !== !!password) { setError('Provide both username and password for an account login.'); return; }
    if (!username && !Object.keys(values).length) { setError('Add an account login or at least one access header.'); return; }
    setBusy(true);
    try { await api(entry ? `/vault/${entry.id}` : '/vault', { method: entry ? 'PUT' : 'POST', body: JSON.stringify({ name, url, credentials: { username, password, headers: values } }) }); setPassword(''); setUsername(''); setHeaders(''); saved(); }
    catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <Dialog close={close} title={entry ? 'Replace saved credentials' : 'Add credentials to vault'}><div className="modal-top"><h2>{entry ? 'Replace credentials' : 'Add credentials'}</h2><button className="icon-button" aria-label="Close credential editor" onClick={close}><X size={20} /></button></div>
    <form onSubmit={submit}><div className="wizard-content vault-editor">
      {entry && <div className="soft-note"><RefreshCw size={16} />Enter the complete replacement login and headers. Saved secrets are never displayed or prefilled.</div>}
      <label>Name<input value={name} onChange={e => setName(e.target.value)} placeholder="Demo shop · QA account" maxLength={100} required autoFocus /></label>
      <label>Website URL<input type="url" value={url} onChange={e => setUrl(e.target.value)} placeholder="http://127.0.0.1:8765" required /><small>Shown first when you audit this website. You can still use it on any other site.</small></label>
      <div className="field-grid"><label>Email / username<input autoComplete="off" value={username} onChange={e => setUsername(e.target.value)} placeholder="qa@example.com" maxLength={254} /></label><label>Password<input type="password" autoComplete="new-password" value={password} onChange={e => setPassword(e.target.value)} placeholder="Test account password" maxLength={1024} /></label></div>
      <details className="advanced"><summary>Access headers (optional)</summary><label>HTTP headers (JSON)<textarea rows={3} spellCheck={false} value={headers} onChange={e => setHeaders(e.target.value)} placeholder={'{"X-Audit-Token": "your-staging-token"}'} /><small>You can save headers on their own, without a login.</small></label></details>
      {error && <ErrorNote message={error} />}
    </div><div className="modal-footer"><button type="button" className="button ghost" onClick={close}>Cancel</button><button className="button primary" disabled={busy}>{busy ? <Spinner /> : <LockKeyhole size={15} />}{entry ? 'Replace credentials' : 'Save to vault'}</button></div></form>
  </Dialog>;
}
