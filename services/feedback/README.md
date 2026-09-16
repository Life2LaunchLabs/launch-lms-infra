# Feedback service

This service owns durable submission/retry coordination while Jira FEED remains
the conversation record. It receives a `TrackerAdapter`; Jira credentials never
enter application containers, browser sessions, or agent child processes.

Accepted submissions first create an idempotency record with `pending` status.
Attachments stream from the request to the tracker adapter. Only after every
attachment and the issue conversation are complete does the operation become
`synchronized`. Partial failures remain visible and retryable.
