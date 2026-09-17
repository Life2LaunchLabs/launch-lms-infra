# Embed protocol v1

`loader.js` is the immutable, application-agnostic host SDK for protocol
`launch-operations/v1`. The managed application supplies only project and
environment identifiers, non-sensitive UI context, and a callback that obtains a
short-lived application-signed token.

The SDK creates a sandboxed iframe at `/embed/v1/` and establishes a
nonce-bound `MessageChannel`. The token crosses that in-memory channel once and
is redeemed into an opaque platform session. It is never added to a URL, browser
storage, DOM attribute, or log. The iframe verifies the real parent origin before
accepting the channel; the API separately verifies the signed token, exact nonce,
project/environment policy, and manifest-approved parent origin. Redeemed nonces
are hashed and unique in PostgreSQL, so replay is rejected.

The iframe owns its toolbar and panel. It reports only open/closed state to the
SDK, which reserves the collapsed slot or promotes the container to a full-page
overlay. Route, theme, viewport, organization, role, and release changes use
versioned channel messages and do not reload the iframe. Closing restores focus
to the host trigger when one exists.

Protocol v1 is a compatibility boundary. Additive v1 messages must tolerate
unknown fields. Breaking transport or claim changes require a new SDK and iframe
path; keep v1 deployed through at least that subsequent protocol release.
