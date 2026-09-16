# Operations embed protocol v1

The host conditionally loads immutable `/sdk/v1/loader.js` and calls
`LaunchOperationsV1.mount`. It supplies project/environment, non-secret context,
and a callback that mints a five-minute application session for the SDK nonce.
The loader owns the collapsed slot and temporary full-viewport overlay.

The iframe URL contains only project and environment. The application token is
delivered once through a transferred `MessagePort`, cleared by the loader, verified
against current/next Ed25519 keys, and exchanged for a 15-minute memory-only
platform session. Tokens never enter URL parameters, fragments, browser storage,
logs, or artifacts. Every application nonce is consumed once in PostgreSQL.

## Messages

Every message contains `protocol: launch-operations/v1` and the handshake nonce.

- Host window → iframe window: `init`, transferring one `MessagePort` to the exact
  control-plane origin.
- Iframe port → host port: `ready`, `open`, `close`, `request_session`.
- Host port → iframe port: `session`, `context`, `unavailable`.

Context contains route, theme, viewport, opaque organization identifier, role, and
release revision. It never contains the Launch bearer token. The iframe validates
the host window origin against the project manifest; its document receives matching
`frame-ancestors` CSP. Normal application behavior remains independent of the SDK.

Protocol v1 remains supported for at least the complete v2 release window. A future
loader uses a new versioned URL and message protocol rather than mutating this file.
