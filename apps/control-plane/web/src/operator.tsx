import { FormEvent, ReactNode, useEffect, useRef, useState } from 'react'

type Operator = { login: string; avatar_url?: string }
type Module = { state: 'available' | 'unavailable'; reason?: string; [key: string]: unknown }
type Dashboard = { project_id: string; repository: string; product: Module; delivery: Module; feedback: Module; ci: Module; operations: Module }
type Goal = { id: string; title: string; outcome?: string; activity_count: number }
type Group = { id: string; title: string; intent?: string; path: string; blob_sha: string; goal_count: number; activity_count: number; step_count: number; goals: Goal[] }
type Catalog = { source_revision: string; fetched_at: string; migration: { expected_counts: Record<string, number> }; groups: Group[] }
type Document = { source_revision: string; fetched_at: string; path: string; blob_sha: string; document: Record<string, unknown> }
type Design = { source_revision: string; fetched_at: string; path: string; blob_sha: string; content: string }
type View = 'overview' | 'product' | 'design'

async function json<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, init)
  if (!response.ok) {
    const error = await response.json().catch(() => ({ detail: `Request failed (${response.status})` }))
    throw new Error(error.detail || `Request failed (${response.status})`)
  }
  return response.json()
}

function StateCard({ title, module, children }: { title: string; module?: Module; children?: ReactNode }) {
  const available = module?.state === 'available'
  return <article className="state-card">
    <div className="card-heading"><h3>{title}</h3><span className={available ? 'state good' : 'state muted'}>{available ? 'Live' : 'Unavailable'}</span></div>
    {available ? children : <p className="secondary">{module?.reason || 'No current data.'}</p>}
  </article>
}

function Overview({ data, status }: { data: Dashboard; status: Module | null }) {
  const counts = data.product.counts as Record<string, number> | undefined
  const delivery = data.delivery as Module & { total?: number; by_status?: Record<string, number> }
  const operations = data.operations as Module & { pending_feedback?: number; runs?: Array<Record<string, unknown>>; deployments?: Array<Record<string, unknown>> }
  const ci = data.ci as Module & { recent?: Array<Record<string, unknown>> }
  const feedback = data.feedback as Module & { total?: number }
  return <div className="dashboard-grid">
    <StateCard title="Product truth" module={data.product}><p className="metric">{counts?.activities ?? 0}<span>activities</span></p><p className="secondary">{counts?.goals ?? 0} goals · {counts?.steps ?? 0} steps</p><code>{String(data.product.source_revision || '').slice(0, 12)}</code></StateCard>
    <StateCard title="Delivery" module={data.delivery}><p className="metric">{delivery.total ?? 0}<span>recent issues</span></p><p className="secondary">{Object.entries(delivery.by_status || {}).map(([name, count]) => `${name} ${count}`).join(' · ')}</p></StateCard>
    <StateCard title="Feedback" module={data.feedback}><p className="metric">{feedback.total ?? 0}<span>recent threads</span></p><p className="secondary">{operations.pending_feedback ?? 0} waiting to synchronize</p></StateCard>
    <StateCard title="Symphony" module={status || undefined}><p className="metric">{operations.runs?.length ?? 0}<span>recorded runs</span></p><p className="secondary">{status?.paused ? 'Dispatch paused' : String(status?.status || 'Ready')}</p></StateCard>
    <StateCard title="Continuous integration" module={data.ci}><ul className="compact-list">{(ci.recent || []).slice(0, 4).map(run => <li key={String(run.id)}><a href={String(run.url)}>{String(run.name || 'Workflow')}</a><span>{String(run.status || 'unknown')}</span></li>)}</ul></StateCard>
    <StateCard title="Deployments" module={data.operations}><ul className="compact-list">{(operations.deployments || []).slice(0, 4).map((item, index) => <li key={index}><span>{String(item.environment)}</span><code>{String(item.source_sha).slice(0, 10)}</code><span>{String(item.status)}</span></li>)}</ul></StateCard>
  </div>
}

function Proposal({ path, blob, revision, initial, onClose }: { path: string; blob: string; revision: string; initial: string; onClose: () => void }) {
  const panel = useRef<HTMLDivElement>(null)
  const [content, setContent] = useState(initial)
  const [title, setTitle] = useState('Clarify product plan')
  const [reason, setReason] = useState('Explain the durable product outcome more clearly.')
  const [result, setResult] = useState<{ url?: string; error?: string; busy?: boolean }>({})
  useEffect(() => {
    function keyboard(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose()
      if (event.key !== 'Tab' || !panel.current) return
      const controls = [...panel.current.querySelectorAll<HTMLElement>('button, a[href], input, textarea')].filter(control => !control.hasAttribute('disabled'))
      if (!controls.length) return
      const first = controls[0]; const last = controls[controls.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    document.addEventListener('keydown', keyboard)
    return () => document.removeEventListener('keydown', keyboard)
  }, [onClose])
  async function submit(event: FormEvent) {
    event.preventDefault(); setResult({ busy: true })
    try {
      const value = await json<{ pull_request: { url: string } }>('/api/v1/projects/launch-lms/planning/proposals', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path, content, expected_blob_sha: blob, base_sha: revision, title, reason }),
      })
      setResult({ url: value.pull_request.url })
    } catch (error) { setResult({ error: error instanceof Error ? error.message : 'Proposal failed.' }) }
  }
  return <div className="editor-panel" role="dialog" aria-modal="true" aria-labelledby="proposal-title">
    <div className="editor-shell" ref={panel}>
      <div className="card-heading"><div><p className="eyebrow">Pull request proposal</p><h2 id="proposal-title">Edit repository truth</h2></div><button className="icon-button" onClick={onClose} aria-label="Close editor" autoFocus>×</button></div>
      <p className="secondary">This creates a new branch and reviewable PR against <code>dev</code>. It never writes directly to a protected branch.</p>
      {result.url ? <div className="success"><strong>Proposal created.</strong><a href={result.url}>Open pull request</a></div> : <form onSubmit={submit} className="proposal-form">
        <label>PR title<input value={title} onChange={event => setTitle(event.target.value)} required minLength={3} /></label>
        <label>Reason<textarea className="short" value={reason} onChange={event => setReason(event.target.value)} required /></label>
        <label>Canonical document<textarea className="code-editor" value={content} onChange={event => setContent(event.target.value)} spellCheck={false} required /></label>
        {result.error && <p className="error" role="alert">{result.error}</p>}
        <button className="button" disabled={result.busy}>{result.busy ? 'Creating proposal…' : 'Create branch and PR'}</button>
      </form>}
    </div>
  </div>
}

function ProductMap({ catalog }: { catalog: Catalog }) {
  const editButton = useRef<HTMLButtonElement>(null)
  const [selected, setSelected] = useState<Document | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState(false)
  function closeEditor() { setEditing(false); requestAnimationFrame(() => editButton.current?.focus()) }
  async function open(group: Group) {
    setError(''); setSelected(null)
    try { setSelected(await json<Document>(`/api/v1/projects/launch-lms/planning/product-map/${group.id}`)) }
    catch (value) { setError(value instanceof Error ? value.message : 'Unable to load this group.') }
  }
  return <div className="product-layout">
    <aside className="group-list" aria-label="Product groups">{catalog.groups.map(group => <button key={group.id} onClick={() => open(group)} className={selected?.path === group.path ? 'selected' : ''}><strong>{group.title}</strong><span>{group.goal_count} goals · {group.activity_count} activities · {group.step_count} steps</span></button>)}</aside>
    <section className="document-view">
      {error && <p className="error" role="alert">{error}</p>}
      {!selected && !error && <div className="empty"><h2>Browse the product map</h2><p>Select a product group to inspect its goals, activities, lifecycle state, acceptance criteria, and references.</p></div>}
      {selected && <><div className="card-heading"><div><p className="eyebrow">{String(selected.document.id)}</p><h2>{String(selected.document.title)}</h2></div><button ref={editButton} className="button secondary-button" onClick={() => setEditing(true)}>Propose edit</button></div><p className="lede">{String(selected.document.intent || '')}</p>
        <div className="goal-list">{((selected.document.goals || []) as Array<Record<string, unknown>>).map(goal => <details key={String(goal.id)}><summary><span><strong>{String(goal.title)}</strong><small>{String(goal.id)}</small></span><span>{(goal.activities as unknown[] || []).length} activities</span></summary><p>{String(goal.outcome || '')}</p><ul>{((goal.activities || []) as Array<Record<string, unknown>>).map(activity => <li key={String(activity.id)}><span>{String(activity.title)}</span><code>{String(activity.product_state || 'unknown')}</code></li>)}</ul></details>)}</div>
        {editing && <Proposal path={selected.path} blob={selected.blob_sha} revision={selected.source_revision} initial={JSON.stringify(selected.document, null, 2) + '\n'} onClose={closeEditor} />}</>}
    </section>
  </div>
}

function DesignIndex({ design }: { design: Design }) {
  const editButton = useRef<HTMLButtonElement>(null)
  const [editing, setEditing] = useState(false)
  function closeEditor() { setEditing(false); requestAnimationFrame(() => editButton.current?.focus()) }
  return <section className="document-view standalone"><div className="card-heading"><div><p className="eyebrow">Repository design index</p><h2>Design history and readiness</h2></div><button ref={editButton} className="button secondary-button" onClick={() => setEditing(true)}>Propose edit</button></div><pre className="markdown-view">{design.content}</pre>{editing && <Proposal path={design.path} blob={design.blob_sha} revision={design.source_revision} initial={design.content} onClose={closeEditor} />}</section>
}

export default function OperatorApp() {
  const [operator, setOperator] = useState<Operator | null | undefined>()
  const [view, setView] = useState<View>('overview')
  const [dashboard, setDashboard] = useState<Dashboard | null>(null)
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [design, setDesign] = useState<Design | null>(null)
  const [status, setStatus] = useState<Module | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    json<Operator>('/api/v1/operator').then(async identity => {
      setOperator(identity)
      const results = await Promise.allSettled([json<Dashboard>('/api/v1/projects/launch-lms/dashboard'), json<Catalog>('/api/v1/projects/launch-lms/planning/product-map'), json<Design>('/api/v1/projects/launch-lms/planning/design'), json<Module>('/api/v1/orchestration/status')])
      if (results[0].status === 'fulfilled') setDashboard(results[0].value)
      if (results[1].status === 'fulfilled') setCatalog(results[1].value)
      if (results[2].status === 'fulfilled') setDesign(results[2].value)
      if (results[3].status === 'fulfilled') setStatus({ ...results[3].value, state: 'available' })
      const failed = results.filter(result => result.status === 'rejected').length
      if (failed) setError(`${failed} operations module${failed === 1 ? '' : 's'} unavailable. Available modules remain usable.`)
    }).catch(() => setOperator(null))
  }, [])
  if (operator === undefined) return <main><p className="loading">Loading operations…</p></main>
  if (!operator) return <main className="signin"><p className="eyebrow">Restricted control plane</p><h1>Launch Operations</h1><p>Sign in with an approved GitHub organization or repository account.</p><a className="button" href="/api/v1/auth/github/login">Sign in with GitHub</a></main>
  return <main><header className="app-header"><div><p className="eyebrow">Control plane</p><h1>Launch Operations</h1><p className="secondary">Product truth, delivery, feedback, deployments, and agents—at the revisions that produced them.</p></div><div className="operator"><span>Signed in as</span><strong>{operator.login}</strong></div></header>
    <nav className="tabs" aria-label="Operations sections">{(['overview', 'product', 'design'] as View[]).map(item => <button key={item} aria-current={view === item ? 'page' : undefined} onClick={() => setView(item)}>{item}</button>)}</nav>
    {error && <p className="module-warning" role="status">{error}</p>}
    <div className="revision"><span>Repository</span><strong>Life2LaunchLabs/launch-lms</strong><span>Source</span><code>{catalog?.source_revision.slice(0, 12) || 'unavailable'}</code><span>Fetched</span><time>{catalog ? new Date(catalog.fetched_at).toLocaleString() : '—'}</time></div>
    {view === 'overview' && (dashboard ? <Overview data={dashboard} status={status} /> : <div className="empty"><h2>Dashboard unavailable</h2><p>Repository planning may still be available from the tabs above.</p></div>)}
    {view === 'product' && (catalog ? <ProductMap catalog={catalog} /> : <div className="empty"><h2>Product map unavailable</h2><p>Refresh after repository access is restored.</p></div>)}
    {view === 'design' && (design ? <DesignIndex design={design} /> : <div className="empty"><h2>Design index unavailable</h2><p>Refresh after repository access is restored.</p></div>)}</main>
}
