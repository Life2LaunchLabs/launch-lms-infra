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
  let port = null
  let nonce = ''
  let context = {}
  let lastTrigger = null
  let platformSession = ''

  function send(message) {
    if (port) port.postMessage(Object.assign({ nonce }, message))
  }

  function unavailable(message) {
    status.textContent = message || 'Project tools unavailable'
    send({ type: 'launch-operations:v1:state', available: false, open: false })
  }

  function renderContext(next) {
    context = Object.assign({}, context, next || {})
    root.dataset.theme = context.theme === 'dark' ? 'dark' : 'light'
  }

  function closePanel() {
    panel.hidden = true
    toolbar.hidden = false
    send({ type: 'launch-operations:v1:state', available: true, open: false })
    if (lastTrigger) lastTrigger.focus()
  }

  function openPanel(name, trigger) {
    const labels = { announcements: 'Announcements', releases: "What's new", feedback: 'Feedback' }
    const descriptions = {
      announcements: 'No announcements are available yet.',
      releases: 'Verified release notes will appear here.',
      feedback: 'Feedback conversations will appear here once synchronization is enabled.',
    }
    const panelName = Object.prototype.hasOwnProperty.call(labels, name) ? name : 'feedback'
    lastTrigger = trigger || null
    title.textContent = labels[panelName]
    content.textContent = descriptions[panelName]
    toolbar.hidden = true
    panel.hidden = false
    send({ type: 'launch-operations:v1:state', available: true, open: true })
    close.focus()
  }

  toolbar.addEventListener('click', event => {
    const button = event.target.closest('button[data-panel]')
    if (button) openPanel(button.dataset.panel, button)
  })
  close.addEventListener('click', closePanel)
  global.document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && !panel.hidden) closePanel()
    if (event.key === 'Tab' && !panel.hidden) {
      const focusable = Array.from(panel.querySelectorAll('button,[href],input,textarea,[tabindex]:not([tabindex="-1"])'))
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
            method: 'POST',
            headers: { Authorization: `Bearer ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ nonce, protocol: PROTOCOL, parent_origin: parentOrigin }),
          })
          if (!response.ok) return unavailable('Project tools session unavailable')
          const redeemed = await response.json()
          platformSession = redeemed.session_token
          if (typeof platformSession !== 'string' || platformSession.length < 32) return unavailable('Project tools session unavailable')
          renderContext(next.context)
          status.hidden = true
          toolbar.hidden = false
          root.removeAttribute('aria-busy')
          send({ type: 'launch-operations:v1:state', available: true, open: false })
        } catch (_) {
          unavailable('Project tools unavailable')
        }
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
