# Feedback service

This service owns durable submission/retry coordination while Jira FEED remains
the conversation record. It receives a `TrackerAdapter`; Jira credentials never
enter application containers, browser sessions, or agent child processes.

Accepted submissions first create an idempotency record with `pending` status.
Attachments stream from the request to the tracker adapter. Only after every
attachment and the issue conversation are complete does the operation become
`synchronized`. Partial failures remain visible and retryable.

The embed API authenticates with a random, memory-only platform session derived
from the one-use application JWT. Submission idempotency keys are bound to the
opaque user and organization. Pending message/context data is retained only to
survive a tracker outage and is removed after synchronization; Jira FEED remains
the durable conversation. Attachment bytes are never written to platform-owned
storage. A partial attachment failure remains `attachment_failed` until the same
client operation is retried with the original files.
