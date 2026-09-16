/* Launch Operations embed protocol v1. This URL is immutable for the v1 lifetime. */
(() => {
  'use strict'
  const PROTOCOL = 'launch-operations/v1'
  const script = document.currentScript
  const platformOrigin = new URL(script && script.src ? script.src : location.href).origin

  function mount(options) {
    if (!options || !options.project || !options.environment || typeof options.getSessionToken !== 'function') {
      throw new TypeError('project, environment, and getSessionToken are required')
    }
    let open = false
    let destroyed = false
    let connecting = false
    let port = null
    let timer = null
    let previouslyFocused = null
    const nonce = crypto.randomUUID()
    const container = document.createElement('div')
    container.dataset.launchOperations = 'v1'
    Object.assign(container.style, {
      position: 'fixed', right: 'max(12px, env(safe-area-inset-right))',
      bottom: 'max(12px, env(safe-area-inset-bottom))', width: 'min(390px, calc(100vw - 24px))',
      height: '52px', zIndex: '2147483000', border: '0', pointerEvents: 'auto',
    })
    const frame = document.createElement('iframe')
    frame.title = 'Project tools'
    frame.referrerPolicy = 'no-referrer'
    frame.sandbox = 'allow-scripts allow-forms allow-same-origin allow-downloads'
    frame.allow = 'clipboard-write'
    frame.src = `${platformOrigin}/embed/v1?project=${encodeURIComponent(options.project)}&environment=${encodeURIComponent(options.environment)}`
    Object.assign(frame.style, { width: '100%', height: '100%', border: '0', borderRadius: '16px', colorScheme: 'light dark' })
    container.append(frame)
    ;(options.parent || document.body).append(container)

    const setOpen = next => {
      open = Boolean(next)
      if (open) {
        previouslyFocused = document.activeElement
        Object.assign(container.style, { inset: '0', width: '100vw', height: '100dvh', right: 'auto', bottom: 'auto' })
      } else {
        Object.assign(container.style, { inset: 'auto', right: 'max(12px, env(safe-area-inset-right))', bottom: 'max(12px, env(safe-area-inset-bottom))', width: 'min(390px, calc(100vw - 24px))', height: '52px' })
        if (previouslyFocused instanceof HTMLElement) previouslyFocused.focus({ preventScroll: true })
      }
    }
    const context = () => ({
      protocol: PROTOCOL, type: 'context',
      route: options.context?.route || `${location.pathname}${location.search}${location.hash}`,
      theme: options.context?.theme || document.documentElement.dataset.theme || 'system',
      viewport: { width: innerWidth, height: innerHeight },
      organization: options.context?.organization || null, role: options.context?.role || null,
      release: options.context?.release || null,
    })
    const sendContext = () => port && port.postMessage(context())
    const sendSession = async () => {
      try {
        let token = await options.getSessionToken({ nonce, protocol: PROTOCOL })
        if (destroyed || !port) return
        port.postMessage({ protocol: PROTOCOL, type: 'session', nonce, token })
        token = null
      } catch (_) {
        port?.postMessage({ protocol: PROTOCOL, type: 'unavailable' })
        if (options.hideWhenUnavailable !== false) container.hidden = true
        options.onUnavailable?.()
      }
    }
    const connect = () => {
      if (destroyed || !frame.contentWindow || port || connecting) return
      connecting = true
      const channel = new MessageChannel()
      channel.port1.onmessage = event => {
        const message = event.data || {}
        if (message.protocol !== PROTOCOL || message.nonce !== nonce) return
        if (message.type === 'ready') {
          clearTimeout(timer); port = channel.port1; port.start(); sendContext(); void sendSession()
        } else if (message.type === 'open') setOpen(true)
        else if (message.type === 'close') setOpen(false)
        else if (message.type === 'request_session') void sendSession()
      }
      frame.contentWindow.postMessage({ protocol: PROTOCOL, type: 'init', nonce }, platformOrigin, [channel.port2])
    }
    frame.addEventListener('load', () => {
      port?.close(); port = null; connecting = false; connect()
      timer = setTimeout(() => {
        if (!port && !destroyed) { connecting = false; container.hidden = options.hideWhenUnavailable !== false; options.onUnavailable?.() }
      }, 5000)
    })
    const update = () => sendContext()
    addEventListener('resize', update, { passive: true })
    addEventListener('popstate', update)
    return {
      update(next) { options.context = { ...options.context, ...next }; sendContext() },
      open(panel = 'feedback') { port?.postMessage({ protocol: PROTOCOL, type: 'open_panel', nonce, panel }) },
      destroy() { destroyed = true; clearTimeout(timer); port?.close(); removeEventListener('resize', update); removeEventListener('popstate', update); container.remove() },
    }
  }

  Object.defineProperty(window, 'LaunchOperationsV1', { value: Object.freeze({ mount, protocol: PROTOCOL }), writable: false })
})()
