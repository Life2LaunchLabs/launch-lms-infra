# Feedback service

This service owns durable submission/retry coordination while Jira FEED remains
the conversation record. It receives a `TrackerAdapter`; Jira credentials never
enter application containers, browser sessions, or agent child processes.

Accepted submissions first create an idempotency record with `pending` status.
Attachments are held only in the control-plane database while an accepted
operation is pending, never in Launch LMS storage. The worker streams them to
Jira, marks the Jira property complete only after every declared image lands,
then deletes the held bytes. Partial failures stay pending and retry by stable
operation and attachment-slot identifiers; incomplete Jira issues are hidden
from the candidate feed.

The service also reads user-scoped Jira conversations, brokers attachment
downloads and tester replies, and records resolution confirmation in Jira.
Unread markers, announcements, synchronization cursors, and deployment-derived
release entries remain operational PostgreSQL state rather than a competing
feedback record.
