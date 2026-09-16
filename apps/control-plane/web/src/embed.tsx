import { useCallback, useEffect, useRef, useState } from 'react'

const PROTOCOL = 'launch-operations/v1'
type Panel = 'feedback' | 'releases' | 'announcements'
type Context = { route?: string; theme?: string; viewport?: { width: number; height: number }; organization?: string; role?: string; release?: string }
type FeedbackItem = { key: string; summary: string; status: string; revision: string; created?: string; comments: { id?: string; body: string; created?: string }[]; attachments: { id?: string; filename?: string; mime_type?: string }[] }
type FeedItem = { id?: string; revision?: string; title?: string; body?: string; status?: string; deployed_at?: string; image_digest?: string }
type Feed = { feedback: FeedbackItem[]; releases: FeedItem[]; announcements: FeedItem[]; unread: Record<Panel, number> }

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
  const panelRef = useRef<HTMLElement | null>(null)

  const loadFeed = useCallback(async (token: string) => {
    const response = await fetch('/api/v1/embed/feed', { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) throw new Error('feed unavailable')
    setFeed(await response.json())
  }, [])

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
    loadFeed(platformToken).catch(() => setUnavailable(true))
  }, [loadFeed, platformToken])

  useEffect(() => {
    if (!panel || !platformToken || feed.unread[panel] === 0) return
    const values = feed[panel].map(value => ({ id: 'key' in value ? value.key : value.id || value.revision, revision: value.revision }))
    fetch('/api/v1/embed/viewed', {
      method: 'POST', headers: { Authorization: `Bearer ${platformToken}`, 'Content-Type': 'application/json' },
      body: JSON.stringify({ category: panel, revisions: values }),
    }).then(response => { if (response.ok) setFeed(current => ({ ...current, unread: { ...current.unread, [panel]: 0 } })) }).catch(() => undefined)
  }, [feed, panel, platformToken])

  useEffect(() => {
    if (panel) closeRef.current?.focus()
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === 'Escape' && panel) close()
      if (event.key !== 'Tab' || !panelRef.current) return
      const focusable = [...panelRef.current.querySelectorAll<HTMLElement>('button:not([disabled]),a[href],textarea,input,select,[tabindex]:not([tabindex="-1"])')]
      if (!focusable.length) return
      const first = focusable[0]; const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus() }
    }
    addEventListener('keydown', keyboard)
    return () => removeEventListener('keydown', keyboard)
  }, [panel])

  const open = (next: Panel) => { setPanel(next); portRef.current?.postMessage({ protocol: PROTOCOL, type: 'open', nonce: nonceRef.current }) }
  const close = () => { setPanel(null); portRef.current?.postMessage({ protocol: PROTOCOL, type: 'close', nonce: nonceRef.current }) }
  const label: Record<Panel, string> = { feedback: 'Feedback', releases: "What's new", announcements: 'Announcements' }

  if (unavailable) return <div className="ops-unavailable" role="status">Tools unavailable</div>
  return <div className={`ops-frame ${context.theme === 'dark' ? 'dark' : ''}`}>
    <nav className="ops-toolbar" aria-label="Project tools">
      {(Object.keys(label) as Panel[]).map(value => <button key={value} aria-expanded={panel === value} onClick={() => open(value)}>{label[value]}{feed.unread[value] ? <span aria-label={`${feed.unread[value]} unread`}>{feed.unread[value]}</span> : null}</button>)}
    </nav>
    {panel ? <section ref={panelRef} className="ops-panel" role="dialog" aria-modal="true" aria-labelledby="ops-title">
      <header><div><p>{project} · {environment}</p><h1 id="ops-title">{label[panel]}</h1></div><button ref={closeRef} onClick={close} aria-label="Close project tools">×</button></header>
      {panel === 'feedback' ? <Feedback token={platformToken} context={context} values={feed.feedback} refresh={() => loadFeed(platformToken)} /> : <Items values={feed[panel]} empty={panel === 'releases' ? 'No new releases.' : 'No announcements.'} />}
    </section> : null}
  </div>
}

function Items({ values, empty }: { values: FeedItem[]; empty: string }) {
  return values.length ? <div className="ops-items">{values.map((value, index) => <article key={value.id || value.revision || index}>
    <h2>{value.title || value.revision?.slice(0, 12) || 'Update'}</h2>
    {value.body ? <p>{value.body}</p> : null}
    {value.status ? <p><strong>Status:</strong> {value.status}</p> : null}
    {value.deployed_at ? <time dateTime={value.deployed_at}>{new Date(value.deployed_at).toLocaleString()}</time> : null}
  </article>)}</div> : <p className="ops-empty">{empty}</p>
}

function Feedback({ token, context, values, refresh }: { token: string; context: Context; values: FeedbackItem[]; refresh: () => Promise<void> }) {
  const [message, setMessage] = useState('')
  const [state, setState] = useState('')
  const [images, setImages] = useState<File[]>([])
  const operationKey = useRef(crypto.randomUUID())
  const submit = async () => {
    if (!message.trim() || !token) return
    setState('Sending…')
    const response = await fetch('/api/v1/embed/feedback', { method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json', 'Idempotency-Key': operationKey.current }, body: JSON.stringify({ message, context, attachment_count: images.length }) })
    if (!response.ok) { setState('Could not save feedback. Your message remains here so you can retry.'); return }
    const result = await response.json()
    for (const [slot, image] of images.entries()) {
      const body = new FormData(); body.append('slot', String(slot)); body.append('image', image)
      const upload = await fetch(`/api/v1/embed/feedback/${result.operation_id}/attachments`, { method: 'POST', headers: { Authorization: `Bearer ${token}` }, body })
      if (!upload.ok) { setState('Feedback is saved, but an image is still pending. Retry to finish sending.'); return }
    }
    setMessage(''); setImages([]); operationKey.current = crypto.randomUUID()
    setState('Feedback saved and waiting to synchronize.'); void refresh()
  }
  return <div className="ops-feedback"><label htmlFor="ops-feedback">What did you notice?</label><textarea id="ops-feedback" value={message} onChange={event => setMessage(event.target.value)} placeholder="A quick sentence is plenty." />
    <label htmlFor="ops-images">Screenshots (up to 3)</label><input id="ops-images" type="file" accept="image/png,image/jpeg,image/webp" multiple onChange={event => setImages([...event.target.files || []].slice(0, 3))} />
    {images.length ? <p>{images.length} image{images.length === 1 ? '' : 's'} selected</p> : null}
    <button disabled={!message.trim() || !token} onClick={submit}>Send feedback</button><p role="status">{state}</p>
    <div className="ops-conversations"><h2>Your feedback</h2>{values.length ? values.map(value => <FeedbackThread key={value.key} value={value} token={token} refresh={refresh} />) : <p>No previous feedback yet.</p>}</div>
  </div>
}

function FeedbackThread({ value, token, refresh }: { value: FeedbackItem; token: string; refresh: () => Promise<void> }) {
  const [reply, setReply] = useState('')
  const [state, setState] = useState('')
  const send = async () => {
    if (!reply.trim()) return
    setState('Sending…')
    const response = await fetch(`/api/v1/embed/feedback/${encodeURIComponent(value.key)}/reply`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ message: reply }),
    })
    if (response.ok) { setReply(''); setState('Reply sent.'); await refresh() } else setState('Reply could not be sent. Your message remains here.')
  }
  const resolve = async (outcome: 'looks_good' | 'still_happening') => {
    setState('Saving…')
    const response = await fetch(`/api/v1/embed/feedback/${encodeURIComponent(value.key)}/resolution`, {
      method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' }, body: JSON.stringify({ outcome }),
    })
    if (response.ok) { setState('Thanks — your update was recorded.'); await refresh() } else setState('Could not record that update. Please retry.')
  }
  const openAttachment = async (id?: string) => {
    if (!id) return
    const response = await fetch(`/api/v1/embed/feedback/${encodeURIComponent(value.key)}/attachments/${encodeURIComponent(id)}`, { headers: { Authorization: `Bearer ${token}` } })
    if (!response.ok) { setState('Could not open that attachment.'); return }
    const url = URL.createObjectURL(await response.blob()); window.open(url, '_blank', 'noopener,noreferrer'); setTimeout(() => URL.revokeObjectURL(url), 60_000)
  }
  return <article className="ops-thread"><header><strong>{value.summary}</strong><span>{value.status}</span></header>
    {value.comments.map(comment => <p key={comment.id || comment.created}>{comment.body.replace(/^\[Launch LMS (reply|tester comment)\]\s*/, '')}</p>)}
    {value.attachments.length ? <ul>{value.attachments.map(item => <li key={item.id}><button onClick={() => openAttachment(item.id)}>{item.filename}</button></li>)}</ul> : null}
    <label htmlFor={`reply-${value.key}`}>Reply</label><textarea id={`reply-${value.key}`} value={reply} onChange={event => setReply(event.target.value)} />
    <button disabled={!reply.trim()} onClick={send}>Send reply</button>
    <div className="ops-resolution" aria-label="Confirm resolution"><button onClick={() => resolve('looks_good')}>Looks good now</button><button onClick={() => resolve('still_happening')}>Still happening</button></div>
    <p role="status">{state}</p>
  </article>
}
