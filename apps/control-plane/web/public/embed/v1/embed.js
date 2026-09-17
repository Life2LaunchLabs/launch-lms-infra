(function startEmbed(global) {
  'use strict'

  const PROTOCOL = 'launch-operations/v1'
  const params = new URLSearchParams(global.location.search)
  const project = params.get('project') || ''
  const environment = params.get('environment') || ''
  const parentOrigin = params.get('parent_origin') || ''
  const root = global.document.getElementById('operations-embed')
  const toolbar = root.querySelector('.toolbar')
  const panel = root.querySelector('.panel')
  const title = root.querySelector('#panel-title')
  const content = root.querySelector('.content')
  const status = root.querySelector('.status')
  const close = root.querySelector('.close')
  const unread = root.querySelector('.unread')
  let port = null
  let nonce = ''
  let context = {}
  let lastTrigger = null
  let platformSession = ''
  let conversations = []
  let pending = null

  function element(tag, className, text) {
    const node = global.document.createElement(tag)
    if (className) node.className = className
    if (text !== undefined) node.textContent = text
    return node
  }

  function send(message) {
    if (port) port.postMessage(Object.assign({ nonce }, message))
  }

  function unavailable(message) {
    status.hidden = false
    status.textContent = message || 'Project tools unavailable'
    send({ type: 'launch-operations:v1:state', available: false, open: false })
  }

  async function api(path, options) {
    if (!platformSession) throw new Error('Project tools session unavailable')
    const request = Object.assign({}, options || {})
    request.headers = Object.assign({}, request.headers || {}, { Authorization: `Session ${platformSession}` })
    const response = await fetch(path, request)
    if (!response.ok) {
      const detail = await response.json().catch(() => ({}))
      throw new Error(detail.detail || `Request failed (${response.status})`)
    }
    return response
  }

  function renderContext(next) {
    context = Object.assign({}, context, next || {})
    root.dataset.theme = context.theme === 'dark' ? 'dark' : 'light'
  }

  function updateUnread() {
    const count = conversations.filter(item => item.has_unread).length
    unread.hidden = count === 0
    unread.textContent = count > 9 ? '9+' : String(count)
  }

  async function loadFeedback() {
    const response = await api('/api/v1/embed/feedback')
    conversations = await response.json()
    updateUnread()
    return conversations
  }

  function field(labelText, control) {
    const label = element('label', 'field')
    label.append(element('span', '', labelText), control)
    return label
  }

  function action(label, handler, primary) {
    const button = element('button', `button${primary ? ' primary' : ''}`, label)
    button.type = 'button'
    button.addEventListener('click', handler)
    return button
  }

  async function markViewed(conversation) {
    if (!conversation.has_unread) return
    try {
      await api(`/api/v1/embed/feedback/${encodeURIComponent(conversation.key)}/viewed`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ revision: conversation.revision }),
      })
      conversation.has_unread = false
      updateUnread()
    } catch (_) {}
  }

  function renderConversation(conversation) {
    const card = element('article', 'card conversation')
    const heading = element('header')
    heading.append(element('h2', '', conversation.message), element('div', 'meta', `${conversation.key} · ${conversation.status}`))
    card.append(heading)
    if (conversation.intent) card.append(element('div', 'meta', `Category: ${conversation.intent}`))
    if (conversation.attachments.length) {
      card.append(element('div', 'meta', `Attachments: ${conversation.attachments.map(item => item.filename).join(', ')}`))
    }
    const entries = element('ol', 'entries')
    conversation.entries.forEach(entry => {
      const item = element('li', `entry ${entry.author}`)
      item.append(element('strong', '', entry.author === 'operator' ? 'Launch LMS' : 'You'), element('p', '', entry.message))
      entries.append(item)
    })
    if (conversation.entries.length) card.append(entries)

    const reply = element('textarea')
    reply.maxLength = 10000
    reply.placeholder = 'Add more detail or answer a question'
    const replyStatus = element('p', 'meta')
    const replyButton = action('Send reply', async () => {
      const message = reply.value.trim()
      if (!message) return
      replyButton.disabled = true
      replyStatus.textContent = 'Sending…'
      try {
        await api(`/api/v1/embed/feedback/${encodeURIComponent(conversation.key)}/reply`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ message }),
        })
        await showFeedback()
      } catch (error) {
        replyStatus.textContent = error.message
        replyButton.disabled = false
      }
    }, true)
    card.append(field('Reply', reply), replyButton, replyStatus)

    const resolution = element('div', 'actions')
    const resolutionStatus = element('p', 'meta')
    const confirm = async outcome => {
      Array.from(resolution.querySelectorAll('button')).forEach(button => { button.disabled = true })
      resolutionStatus.textContent = 'Updating…'
      try {
        await api(`/api/v1/embed/feedback/${encodeURIComponent(conversation.key)}/resolution`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ outcome }),
        })
        await showFeedback()
      } catch (error) {
        resolutionStatus.textContent = error.message
        Array.from(resolution.querySelectorAll('button')).forEach(button => { button.disabled = false })
      }
    }
    resolution.append(action('This looks good', () => confirm('looks_good')), action('Still happening', () => confirm('still_happening')))
    card.append(resolution, resolutionStatus)
    markViewed(conversation)
    return card
  }

  function submissionForm() {
    const card = element('form', 'card stack')
    const heading = element('h2', '', 'Share feedback')
    const message = element('textarea')
    message.name = 'message'; message.required = true; message.maxLength = 10000
    const intent = element('select')
    intent.name = 'intent'
    ;[['', 'Choose a category'], ['stuck', "I'm stuck"], ['broken', "Something's broken"], ['confusing', "It's confusing"], ['missing', "Something's missing"], ['love', 'I like this']].forEach(([value, label]) => {
      const option = element('option', '', label); option.value = value; intent.append(option)
    })
    const images = element('input')
    images.type = 'file'; images.name = 'images'; images.accept = 'image/png,image/jpeg,image/webp'; images.multiple = true
    const submit = element('button', 'button primary', pending ? 'Retry synchronization' : 'Send feedback')
    submit.type = 'submit'
    const formStatus = element('p', pending ? 'notice' : 'meta', pending ? 'Feedback accepted. Waiting to finish Jira synchronization.' : '')
    if (pending) {
      message.value = pending.message; intent.value = pending.intent || ''
      message.disabled = true; intent.disabled = true; images.disabled = true
      if (pending.files.length) formStatus.textContent += ` ${pending.files.length} screenshot${pending.files.length === 1 ? '' : 's'} retained for retry.`
    }
    card.append(heading, field('What happened?', message), field('Category', intent), field('Screenshots (up to 3)', images), submit, formStatus)
    card.addEventListener('submit', async event => {
      event.preventDefault()
      if (images.files.length > 3) { formStatus.textContent = 'Attach up to three screenshots.'; return }
      submit.disabled = true
      formStatus.className = 'meta'; formStatus.textContent = pending ? 'Retrying…' : 'Sending…'
      if (!pending) pending = {
        id: global.crypto.randomUUID(), message: message.value.trim(), intent: intent.value,
        files: Array.from(images.files), context: JSON.stringify(context),
      }
      const data = new FormData()
      data.set('message', pending.message); if (pending.intent) data.set('intent', pending.intent)
      data.set('context', pending.context)
      pending.files.forEach(file => data.append('images', file))
      try {
        const response = await api('/api/v1/embed/feedback', {
          method: 'POST', headers: { 'Idempotency-Key': `embed-${pending.id}` }, body: data,
        })
        const result = await response.json()
        if (response.status === 202 || result.status === 'pending') {
          await showFeedback()
          return
        }
        pending = null
        await showFeedback()
      } catch (error) {
        formStatus.className = 'notice'; formStatus.textContent = `${error.message}. Your text remains here so you can retry.`
        message.disabled = true; intent.disabled = true; images.disabled = true
        submit.textContent = 'Retry synchronization'; submit.disabled = false
      }
    })
    return card
  }

  async function showFeedback() {
    const stack = element('div', 'stack')
    stack.append(submissionForm(), element('p', 'meta', 'Loading feedback history…'))
    content.replaceChildren(stack)
    try {
      await loadFeedback()
      stack.lastChild.remove()
      if (conversations.length) conversations.forEach(item => stack.append(renderConversation(item)))
      else stack.append(element('p', 'meta', 'Your feedback conversations will appear here.'))
    } catch (error) {
      const notice = element('p', 'notice', `${error.message}. Launch LMS is still available; try again later.`)
      stack.lastChild.remove()
      stack.append(notice, action('Try history again', showFeedback, true))
    }
  }

  async function showAnnouncements() {
    content.replaceChildren(element('p', 'meta', 'Loading announcements…'))
    try {
      const response = await api('/api/v1/embed/announcements')
      const items = await response.json()
      if (!items.length) {
        content.replaceChildren(element('p', 'meta', 'No announcements are available yet.'))
        return
      }
      const stack = element('div', 'stack')
      items.forEach(item => {
        const card = element('article', 'card stack')
        card.append(element('h2', '', item.title), element('p', '', item.body))
        card.append(element('time', 'meta', new Date(item.published_at).toLocaleDateString()))
        stack.append(card)
      })
      content.replaceChildren(stack)
    } catch (_) {
      content.replaceChildren(element('p', 'notice', 'Announcements are temporarily unavailable. Launch LMS is unaffected.'), action('Try again', showAnnouncements, true))
    }
  }

  function closePanel() {
    panel.hidden = true
    toolbar.hidden = false
    send({ type: 'launch-operations:v1:state', available: true, open: false })
    if (lastTrigger) lastTrigger.focus()
  }

  function openPanel(name, trigger) {
    const labels = { announcements: 'Announcements', releases: "What's new", feedback: 'Feedback' }
    const descriptions = { releases: 'Verified release notes will appear here.' }
    const panelName = Object.prototype.hasOwnProperty.call(labels, name) ? name : 'feedback'
    lastTrigger = trigger || null
    title.textContent = labels[panelName]
    content.replaceChildren(element('p', 'meta', descriptions[panelName] || 'Loading feedback…'))
    toolbar.hidden = true
    panel.hidden = false
    send({ type: 'launch-operations:v1:state', available: true, open: true })
    close.focus()
    if (panelName === 'feedback') showFeedback()
    if (panelName === 'announcements') showAnnouncements()
  }

  toolbar.addEventListener('click', event => {
    const button = event.target.closest('button[data-panel]')
    if (button) openPanel(button.dataset.panel, button)
  })
  close.addEventListener('click', closePanel)
  global.document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) closePanel()
    if (event.key === 'Tab' && !panel.hidden) {
      const focusable = Array.from(panel.querySelectorAll('button:not(:disabled),[href],input:not(:disabled),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])'))
      if (!focusable.length) return
      const first = focusable[0]; const last = focusable[focusable.length - 1]
      if (event.shiftKey && global.document.activeElement === first) { event.preventDefault(); last.focus() }
      else if (!event.shiftKey && global.document.activeElement === last) { event.preventDefault(); first.focus() }
    }
  })

  global.addEventListener('message', event => {
    const message = event.data || {}
    if (port || event.origin !== parentOrigin || event.source !== global.parent ||
        message.type !== 'launch-operations:v1:connect' || message.protocol !== PROTOCOL ||
        !/^[0-9a-f-]{16,64}$/.test(message.nonce) || event.ports.length !== 1) return
    nonce = message.nonce
    port = event.ports[0]
    port.onmessage = async portEvent => {
      const next = portEvent.data || {}
      if (next.type === 'launch-operations:v1:session') {
        if (next.protocol !== PROTOCOL || !/^[0-9a-f-]{16,64}$/.test(next.nonce)) return
        nonce = next.nonce
        const token = next.token
        next.token = undefined
        try {
          const response = await fetch('/api/v1/embed/session', {
            method: 'POST', headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ nonce, protocol: PROTOCOL, parent_origin: parentOrigin }),
          })
          if (!response.ok) return unavailable('Project tools session unavailable')
          const redeemed = await response.json()
          platformSession = redeemed.session_token
          if (typeof platformSession !== 'string' || platformSession.length < 32) return unavailable('Project tools session unavailable')
          renderContext(next.context)
          status.hidden = true; toolbar.hidden = false; root.removeAttribute('aria-busy')
          send({ type: 'launch-operations:v1:state', available: true, open: false })
          loadFeedback().catch(() => {})
        } catch (_) { unavailable('Project tools unavailable') }
      } else if (next.nonce !== nonce || next.protocol !== PROTOCOL) return
      else if (next.type === 'launch-operations:v1:context') renderContext(next.context)
      else if (next.type === 'launch-operations:v1:command' && next.command === 'open') openPanel(next.panel)
    }
    port.start()
    send({ type: 'launch-operations:v1:ready' })
  })

  if (params.get('protocol') !== PROTOCOL || !/^https?:\/\//.test(parentOrigin) || !project || !environment) {
    unavailable('Project tools configuration is invalid')
  }
})(window)
