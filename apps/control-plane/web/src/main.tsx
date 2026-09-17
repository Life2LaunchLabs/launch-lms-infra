import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

type Operator = { login: string }
type Project = { display_name: string; repository: string; environments: Record<string, unknown> }
type Work = { issue: string; issue_url: string; state: string; started_at?: string | null; last_event_at?: string | null; due_at?: string | null; blocked_at?: string | null; turns?: number | null; attempt?: number | null; reason?: string }
type Status = { availability: 'live' | 'stale'; generated_at: string | null; observed_at: string; counts: { running: number; retrying: number; blocked: number }; running: Work[]; retrying: Work[]; blocked: Work[] }

function time(value?: string | null) {
  if (!value) return 'Time unavailable'
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? 'Time unavailable' : date.toLocaleString()
}

function WorkList({ items, empty }: { items: Work[]; empty: string }) {
  if (!items.length) return <p className="muted">{empty}</p>
  return <ul className="work-list">{items.map(item => <li key={`${item.state}-${item.issue}`}>
    <div><a href={item.issue_url} target="_blank" rel="noopener noreferrer">{item.issue} ↗</a><span className="badge">{item.state}</span></div>
    <p>{item.state === 'running' ? `Started ${time(item.started_at)} · ${item.turns ?? 'Unknown'} turns` : item.state === 'retrying' ? `${item.reason ?? 'Retry scheduled'} · attempt ${item.attempt ?? 'unknown'}` : item.reason}</p>
    <small>{item.state === 'running' ? `Last event ${time(item.last_event_at)}` : item.state === 'retrying' ? `Due ${time(item.due_at)}` : `Blocked ${time(item.blocked_at)}`}</small>
  </li>)}</ul>
}

function App() {
  const [operator, setOperator] = useState<Operator | null | undefined>()
  const [project, setProject] = useState<Project | null>(null)
  const [status, setStatus] = useState<Status | null>(null)
  const [statusError, setStatusError] = useState('')
  const [projectError, setProjectError] = useState('')

  useEffect(() => {
    let active = true
    async function refreshStatus() {
      try {
        const response = await fetch('/api/v1/orchestration/status', { cache: 'no-store' })
        if (!response.ok) throw new Error('Symphony unavailable')
        const data: Status = await response.json()
        if (active) { setStatus(data); setStatusError('') }
      } catch {
        if (active) { setStatus(null); setStatusError('Symphony status is unavailable. Work cannot be confirmed until the connection recovers.') }
      }
    }
    async function load() {
      try {
        const response = await fetch('/api/v1/operator', { cache: 'no-store' })
        if (!response.ok) { if (active) setOperator(null); return }
        if (active) setOperator(await response.json())
        const projectResponse = await fetch('/api/v1/projects/launch-lms', { cache: 'no-store' })
        if (!projectResponse.ok) throw new Error('Project unavailable')
        if (active) setProject(await projectResponse.json())
      } catch {
        if (active) setProjectError('Project registration is unavailable.')
      }
      await refreshStatus()
    }
    void load()
    const timer = window.setInterval(() => { if (active) void refreshStatus() }, 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])

  if (operator === undefined) return <main><p role="status">Loading operations…</p></main>
  if (!operator) return <main className="sign-in"><p className="eyebrow">Life2Launch</p><h1>Operations</h1><p>Sign in with an approved GitHub account to view delivery status.</p><a className="button" href="/api/v1/auth/github/login">Sign in with GitHub</a></main>
  return <main>
    <header><div><p className="eyebrow">Life2Launch · operations</p><h1>Delivery overview</h1><p className="lead">Live agent activity and deployment evidence for the registered project.</p></div><div className="operator">Signed in as <strong>{operator.login}</strong><form action="/api/v1/auth/logout" method="post"><button type="submit" className="text-button">Sign out</button></form></div></header>
    <section aria-labelledby="project-title"><div className="section-head"><div><p className="eyebrow">Project</p><h2 id="project-title">{project?.display_name ?? 'Project unavailable'}</h2></div><span className="badge neutral">Read-only</span></div>
      {project ? <p><a href={`https://github.com/${project.repository}`} target="_blank" rel="noopener noreferrer">{project.repository} ↗</a><span className="muted"> · {Object.keys(project.environments).join(' · ')}</span></p> : <p role="status">{projectError || 'Loading project…'}</p>}
    </section>
    <section aria-labelledby="agent-title"><div className="section-head"><div><p className="eyebrow">Agent</p><h2 id="agent-title">Symphony</h2></div><span className={`badge ${status?.availability === 'live' ? 'live' : 'warning'}`}>{status?.availability ?? 'Unavailable'}</span></div>
      {statusError ? <p role="alert">{statusError}</p> : status ? <><p className="muted">Snapshot {time(status.generated_at)} · checked {time(status.observed_at)}{status.availability === 'stale' ? ' · Status may be out of date' : ''}</p><div className="columns"><div><h3>Running <span>{status.counts.running}</span></h3><WorkList items={status.running} empty="No active agent run is reported." /></div><div><h3>Retrying <span>{status.counts.retrying}</span></h3><WorkList items={status.retrying} empty="No retry is scheduled." /></div><div><h3>Blocked <span>{status.counts.blocked}</span></h3><WorkList items={status.blocked} empty="No blocked agent run is reported." /></div></div><p className="muted">This live snapshot does not prove a completed attempt; historical evidence requires the attempt journal.</p></> : <p role="status">Loading Symphony status…</p>}
    </section>
    <section aria-labelledby="deployment-title"><div className="section-head"><div><p className="eyebrow">Deployment</p><h2 id="deployment-title">Verification</h2></div><span className="badge warning">Incomplete</span></div><p>Deployment is not attested. Candidate artifact, successful workflow run, and host-observed source SHA and image digest must agree before this view can report a deployment.</p><p className="muted">The protected infrastructure workflow remains the deployment authority.</p></section>
  </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
