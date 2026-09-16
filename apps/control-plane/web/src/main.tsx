import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'
import EmbedApp from './embed'

type Operator = { login: string; avatar_url?: string }
type Status = { status?: string; paused?: boolean; running?: unknown; queued?: unknown }

function App() {
  const [operator, setOperator] = useState<Operator | null | undefined>()
  const [status, setStatus] = useState<Status | null>(null)
  useEffect(() => {
    fetch('/api/v1/operator').then(async response => {
      if (!response.ok) return setOperator(null)
      setOperator(await response.json())
      const state = await fetch('/api/v1/orchestration/status')
      if (state.ok) setStatus(await state.json())
    }).catch(() => setOperator(null))
  }, [])
  if (operator === undefined) return <main><p>Loading operations…</p></main>
  if (!operator) return <main><h1>Launch Operations</h1><p>Operator access is restricted.</p><a className="button" href="/api/v1/auth/github/login">Sign in with GitHub</a></main>
  return <main>
    <header><div><p className="eyebrow">Control plane</p><h1>Launch Operations</h1></div><p>Signed in as <strong>{operator.login}</strong></p></header>
    <section><h2>Symphony</h2>{status ? <pre>{JSON.stringify(status, null, 2)}</pre> : <p>Status unavailable.</p>}</section>
    <section><h2>Platform foundation</h2><p>Project registration, delivery observation, feedback operations, and planning views are enabled incrementally.</p></section>
  </main>
}

const application = location.pathname === '/embed/v1' ? <EmbedApp /> : <App />
createRoot(document.getElementById('root')!).render(<React.StrictMode>{application}</React.StrictMode>)
