import { useEffect, useRef, useState } from 'react'

const PROTOCOL = 'launch-operations/v1'
type Panel = 'feedback' | 'releases' | 'announcements'
type Context = { route?: string; theme?: string; viewport?: { width: number; height: number }; organization?: string; role?: string; release?: string }
type Feed = { feedback: unknown[]; releases: unknown[]; announcements: unknown[]; unread: Record<Panel, number> }

function allowed(origin: string, patterns: string[]) {
  return patterns.some(pattern => origin === pattern || (pattern.includes('*.') && origin.startsWith(pattern.split('*.')[0]) && origin.slice(pattern.split('*.')[0].length).endsWith(`.${pattern.split('*.')[1]}`)))
}

export default function EmbedApp() {
  const query = new URLSearchParams(location.search)
  const project = query.get('project') || ''
  const environment = query.get('environment') || ''
  const [panel, setPanel] = useState<Panel | null>(null)
  const [context, setContext] = useState<Context>({})
  const [platformToken, setPlatformToken] = useState('')
  const [feed, setFeed] = useState<Feed>({ feedback: [], releases: [], announcements: [], unread: { feedback: 0, releases: 0, announcements: 0 } })
  const [unavailable, setUnavailable] = useState(false)
  const portRef = useRef<MessagePort | null>(null)
  const nonceRef = useRef('')
  const closeRef = useRef<HTMLButtonElement | null>(null)

  useEffect(() => {
    document.body.dataset.embed = 'true'
    let active = true
    let hostOrigin = ''
    let nonce = ''
    const configure = async () => {
      const response = await fetch(`/api/v1/embed/config?project=${encodeURIComponent(project)}&environment=${encodeURIComponent(environment)}`)
      if (!response.ok) throw new Error('embed config unavailable')
      const config = await response.json()
      const receiveInit = (event: MessageEvent) => {
        const message = event.data || {}
        if (!active || message.protocol !== PROTOCOL || message.type !== 'init' || !event.ports[0] || !allowed(event.origin, config.allowed_origins)) return
        hostOrigin = event.origin; nonce = message.nonce; nonceRef.current = nonce
        const port = event.ports[0]
        portRef.current?.close(); portRef.current = port
        port.onmessage = async portEvent => {
          const value = portEvent.data || {}
          if (value.protocol !== PROTOCOL || value.nonce !== nonce) return
          if (value.type === 'context') setContext(value)
          if (value.type === 'open_panel' && ['feedback', 'releases', 'announcements'].includes(value.panel)) open(value.panel as Panel)
          if (value.type === 'unavailable') setUnavailable(true)
          if (value.type === 'session') {
            const verified = await fetch('/api/v1/embed/session/verify', {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ project, environment, nonce, host_origin: hostOrigin, token: value.token }),
            })
            if (!verified.ok) { setUnavailable(true); return }
            setPlatformToken((await verified.json()).platform_token)
          }
        }
        port.start(); port.postMessage({ protocol: PROTOCOL, type: 'ready', nonce })
      }
      addEventListener('message', receiveInit)
      return () => removeEventListener('message', receiveInit)
    }
    let remove: (() => void) | undefined
    configure().then(cleanup => { remove = cleanup }).catch(() => setUnavailable(true))
    return () => { active = false; remove?.(); portRef.current?.close() }
  }, [project, environment])

  useEffect(() => {
    if (!platformToken) return
    fetch('/api/v1/embed/feed', { headers: { Authorization: `Bearer ${platformToken}` } })
      .then(response => response.ok ? response.json() : Promise.reject())
      .then(setFeed).catch(() => setUnavailable(true))
  }, [platformToken])

  useEffect(() => {
    if (panel) closeRef.current?.focus()
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape' && panel) close() }
    addEventListener('keydown', escape)
    return () => removeEventListener('keydown', escape)
  }, [panel])

  const open = (next: Panel) => { setPanel(next); portRef.current?.postMessage({ protocol: PROTOCOL, type: 'open', nonce: nonceRef.current }) }
  const close = () => { setPanel(null); portRef.current?.postMessage({ protocol: PROTOCOL, type: 'close', nonce: nonceRef.current }) }
  const label: Record<Panel, string> = { feedback: 'Feedback', releases: "What's new", announcements: 'Announcements' }

  if (unavailable) return <div className="ops-unavailable" role="status">Tools unavailable</div>
  return <div className={`ops-frame ${context.theme === 'dark' ? 'dark' : ''}`}>
    <nav className="ops-toolbar" aria-label="Project tools">
      {(Object.keys(label) as Panel[]).map(value => <button key={value} aria-expanded={panel === value} onClick={() => open(value)}>{label[value]}{feed.unread[value] ? <span aria-label={`${feed.unread[value]} unread`}>{feed.unread[value]}</span> : null}</button>)}
    </nav>
    {panel ? <section className="ops-panel" role="dialog" aria-modal="true" aria-labelledby="ops-title">
      <header><div><p>{project} · {environment}</p><h1 id="ops-title">{label[panel]}</h1></div><button ref={closeRef} onClick={close} aria-label="Close project tools">×</button></header>
      {panel === 'feedback' ? <Feedback token={platformToken} context={context} /> : <Items values={feed[panel]} empty={panel === 'releases' ? 'No new releases.' : 'No announcements.'} />}
    </section> : null}
  </div>
}

function Items({ values, empty }: { values: unknown[]; empty: string }) {
  return values.length ? <pre>{JSON.stringify(values, null, 2)}</pre> : <p className="ops-empty">{empty}</p>
}

function Feedback({ token, context }: { token: string; context: Context }) {
  const [message, setMessage] = useState('')
  const [state, setState] = useState('')
  const submit = async () => {
    if (!message.trim() || !token) return
    setState('Sending…')
    const response = await fetch('/api/v1/embed/feedback', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() }, body: JSON.stringify({ message, context }) })
    if (response.ok) { const result = await response.json(); setMessage(''); setState(result.status === 'pending' ? 'Feedback saved and waiting to synchronize.' : 'Feedback received.') } else setState('Could not send feedback. Your message remains here so you can retry.')
  }
  return <div className="ops-feedback"><label htmlFor="ops-feedback">What did you notice?</label><textarea id="ops-feedback" value={message} onChange={event => setMessage(event.target.value)} placeholder="A quick sentence is plenty." /><button disabled={!message.trim() || !token} onClick={submit}>Send feedback</button><p role="status">{state}</p></div>
}
