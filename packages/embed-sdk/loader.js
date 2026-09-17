(function installLaunchOperations(global) {
  'use strict'

  const PROTOCOL = 'launch-operations/v1'
  const READY = 'launch-operations:v1:ready'
  const SESSION = 'launch-operations:v1:session'
  const CONTEXT = 'launch-operations:v1:context'
  const COMMAND = 'launch-operations:v1:command'
  const STATE = 'launch-operations:v1:state'
  const currentScript = global.document.currentScript
  const sdkOrigin = currentScript ? new URL(currentScript.src, global.location.href).origin : ''

  function nonce() {
    if (global.crypto && typeof global.crypto.randomUUID === 'function') return global.crypto.randomUUID()
    const bytes = new Uint8Array(16)
    global.crypto.getRandomValues(bytes)
    return Array.from(bytes, value => value.toString(16).padStart(2, '0')).join('')
  }

  function mount(options) {
    if (!sdkOrigin || !options || typeof options.getSessionToken !== 'function') {
      throw new Error('Launch Operations requires its SDK origin and a session-token provider')
    }
    const project = String(options.project || '')
    const environment = String(options.environment || '')
    if (!/^[a-z][a-z0-9-]{1,62}$/.test(project) || !/^[a-z][a-z0-9-]{1,31}$/.test(environment)) {
      throw new Error('Launch Operations project or environment is invalid')
    }

    const handshakeNonce = nonce()
    let activeNonce = handshakeNonce
    const channel = new MessageChannel()
    const root = global.document.createElement('div')
    const frame = global.document.createElement('iframe')
    const previousPadding = global.document.documentElement.style.paddingBottom
    let context = Object.assign({}, options.context || {})
    let destroyed = false
    let connected = false
    let openedBy = null
    let unavailableReported = false
    let renewal = 0

    root.dataset.launchOperationsRoot = 'v1'
    root.style.cssText = 'position:fixed;right:0;bottom:0;left:0;height:64px;z-index:2147483000;pointer-events:none;'
    frame.title = 'Project tools'
    frame.sandbox = 'allow-scripts allow-same-origin allow-forms allow-downloads'
    frame.allow = 'clipboard-read; clipboard-write'
    frame.referrerPolicy = 'no-referrer'
    frame.style.cssText = 'display:block;width:100%;height:100%;border:0;background:transparent;pointer-events:auto;'
    const target = new URL('/embed/v1/', sdkOrigin)
    target.searchParams.set('project', project)
    target.searchParams.set('environment', environment)
    target.searchParams.set('parent_origin', global.location.origin)
    target.searchParams.set('protocol', PROTOCOL)
    frame.src = target.toString()
    root.append(frame)
    global.document.body.append(root)
    global.document.documentElement.style.paddingBottom = '64px'

    function reportUnavailable() {
      if (unavailableReported || destroyed) return
      unavailableReported = true
      if (options.hideWhenUnavailable !== false) root.hidden = true
      if (typeof options.onUnavailable === 'function') options.onUnavailable()
    }

    function send(message) {
      if (!destroyed) channel.port1.postMessage(message)
    }

    async function issueSession(sessionNonce) {
      try {
        const token = await options.getSessionToken({ nonce: sessionNonce, protocol: PROTOCOL })
        if (destroyed || typeof token !== 'string' || !token) return reportUnavailable()
        activeNonce = sessionNonce
        send({ type: SESSION, protocol: PROTOCOL, nonce: sessionNonce, token, context })
        global.clearTimeout(renewal)
        renewal = global.setTimeout(() => issueSession(nonce()), 180000)
      } catch (_) {
        reportUnavailable()
      }
    }

    channel.port1.onmessage = async event => {
      const message = event.data || {}
      if (message.nonce !== activeNonce) return
      if (message.type === READY && !connected) {
        connected = true
        await issueSession(handshakeNonce)
      } else if (message.type === STATE) {
        if (message.available === false) return reportUnavailable()
        const open = message.open === true
        root.style.height = open ? '100dvh' : '64px'
        root.style.top = open ? '0' : 'auto'
        if (!open && openedBy && typeof openedBy.focus === 'function') openedBy.focus()
        if (!open) openedBy = null
      }
    }
    channel.port1.start()

    frame.addEventListener('load', () => {
      if (!frame.contentWindow || destroyed) return reportUnavailable()
      frame.contentWindow.postMessage(
        { type: 'launch-operations:v1:connect', protocol: PROTOCOL, nonce: handshakeNonce },
        sdkOrigin,
        [channel.port2],
      )
    }, { once: true })
    frame.addEventListener('error', reportUnavailable, { once: true })
    const timeout = global.setTimeout(() => { if (!connected) reportUnavailable() }, 10000)

    return {
      protocol: PROTOCOL,
      update(next) {
        context = Object.assign({}, context, next || {})
        if (connected) send({ type: CONTEXT, protocol: PROTOCOL, nonce: handshakeNonce, context })
      },
      open(panel) {
        openedBy = global.document.activeElement
        send({ type: COMMAND, protocol: PROTOCOL, nonce: handshakeNonce, command: 'open', panel: panel || 'feedback' })
      },
      destroy() {
        if (destroyed) return
        destroyed = true
        global.clearTimeout(timeout)
        global.clearTimeout(renewal)
        channel.port1.close()
        root.remove()
        global.document.documentElement.style.paddingBottom = previousPadding
      },
    }
  }

  if (!global.LaunchOperationsV1) Object.defineProperty(global, 'LaunchOperationsV1', {
    value: Object.freeze({ protocol: PROTOCOL, mount }), configurable: false, writable: false,
  })
})(window)
