# Candidate surface parity reference

The external surface preserves the current Launch candidate experience before any
new visual direction is introduced.

Source references at migration start:

- `launch-lms/apps/web/components/Candidate/CandidateExperience.tsx`
- `launch-lms/apps/web/components/Candidate/CandidateToolbar.tsx`
- `launch-lms/apps/web/components/Objects/Modals/FeedbackModal.tsx`
- `launch-lms/apps/web/components/Admin/CandidateFeedbackQueue.tsx`
- `launch-lms/apps/web/tests/ui/candidate-feedback.spec.ts`
- `launch-lms/apps/web/tests/ui/candidate-viewport.spec.ts`
- `launch-lms/scripts/docs/BOT-168-candidate-feedback-verification.md`
- `launch-lms/scripts/docs/BOT-210-feedback-verification.md`

Required review states are collapsed/open feedback, feedback history/detail/reply,
zero-to-three attachment previews, resolution confirmation, releases,
announcements, unread badges, pending/retry, and tools-unavailable. Capture learner
light and administrator dark layouts at 1440×900 and 390×844. Verify focus entry,
Tab containment, Escape close, focus restoration, reduced motion, long content,
responsive resizing, route/theme changes without reload, and application navigation
during a control-plane outage.

The current scaffold establishes the toolbar/panel hierarchy and protocol behavior.
It is not parity-complete until Jira conversations, attachment streaming, unread
state, resolution, release manifests, announcements, operator workflow, and the
browser comparison report all pass.
