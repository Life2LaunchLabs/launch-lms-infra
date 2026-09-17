import React, { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import './styles.css'

type Operator = { login: string; avatar_url?: string }
type Status = { status?: string; paused?: boolean; running?: unknown; queued?: unknown }
type Announcement = { id: string; title: string; body: string; published_at: string | null }
const announcementsPath = '/api/v1/projects/launch-lms/environments/unstable/announcements'

function App() {
  const [operator, setOperator] = useState<Operator | null | undefined>()
  const [status, setStatus] = useState<Status | null>(null)
  const [announcements, setAnnouncements] = useState<Announcement[]>([])
  const [title, setTitle] = useState('')
  const [body, setBody] = useState('')
  const [message, setMessage] = useState('')
  async function refreshAnnouncements() {
    const response = await fetch(announcementsPath)
    if (!response.ok) throw new Error('Could not load announcements')
    setAnnouncements(await response.json())
  }
  async function createAnnouncement(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setMessage('Saving draft…')
    try {
      const response = await fetch(announcementsPath, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ title, body }),
      })
      if (!response.ok) throw new Error('Could not save draft')
      setTitle(''); setBody(''); setMessage('Draft saved; publish it when ready.')
      await refreshAnnouncements()
    } catch (error) { setMessage(String(error)) }
  }
  async function publishAnnouncement(id: string) {
    setMessage('Publishing…')
    try {
      const response = await fetch(`${announcementsPath}/${encodeURIComponent(id)}/publish`, { method: 'POST' })
      if (!response.ok) throw new Error('Could not publish announcement')
      setMessage('Published to the unstable candidate surface.')
      await refreshAnnouncements()
    } catch (error) { setMessage(String(error)) }
  }
  useEffect(() => {
    fetch('/api/v1/operator').then(async response => {
      if (!response.ok) return setOperator(null)
      setOperator(await response.json())
      const state = await fetch('/api/v1/orchestration/status')
      if (state.ok) setStatus(await state.json())
      await refreshAnnouncements().catch(() => setMessage('Announcements unavailable.'))
    }).catch(() => setOperator(null))
  }, [])
  if (operator === undefined) return <main><p>Loading operations…</p></main>
  if (!operator) return <main><h1>Launch Operations</h1><p>Operator access is restricted.</p><a className="button" href="/api/v1/auth/github/login">Sign in with GitHub</a></main>
  return <main>
    <header><div><p className="eyebrow">Control plane</p><h1>Launch Operations</h1></div><p>Signed in as <strong>{operator.login}</strong></p></header>
    <section><h2>Symphony</h2>{status ? <pre>{JSON.stringify(status, null, 2)}</pre> : <p>Status unavailable.</p>}</section>
    <section><h2>Unstable announcements</h2>
      <form onSubmit={createAnnouncement}>
        <label>Title <input required maxLength={200} value={title} onChange={event => setTitle(event.target.value)} /></label>
        <label>Message <textarea required maxLength={10000} value={body} onChange={event => setBody(event.target.value)} /></label>
        <button type="submit">Save draft</button>
      </form>
      <p role="status">{message}</p>
      {announcements.map(item => <article key={item.id}><h3>{item.title}</h3><p>{item.body}</p>
        {item.published_at ? <p>Published {new Date(item.published_at).toLocaleString()}</p> :
          <button type="button" onClick={() => publishAnnouncement(item.id)}>Publish to unstable</button>}
      </article>)}
    </section>
    <section><h2>Platform foundation</h2><p>Project registration, delivery observation, feedback operations, and planning views are enabled incrementally.</p></section>
  </main>
}

createRoot(document.getElementById('root')!).render(<React.StrictMode><App /></React.StrictMode>)
