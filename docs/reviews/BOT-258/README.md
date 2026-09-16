# BOT-258 control-plane planning UI review

## Reference and scope

- Reference: the existing control-plane shell at PR 42 revision `334c4b1`, plus the
  BOT-258 design-readiness note. No owner-selected control-plane mockup exists;
  this is an agent-designed operational layout for owner review.
- Affected surfaces: authenticated overview dashboard, repository product-map
  browser, repository design index, and branch/PR proposal dialog.
- Product content remains app-repository data. The platform UI does not establish
  a screenshot or database as a new source of truth.

## Reproducible scenario

The Vite application ran locally with Playwright network routes supplying synthetic
operator, product-map, Jira summary, CI, deployment, feedback, and Symphony data.
No real credentials, issue content, learner data, or application tokens were used.

- Browser: headless Chromium
- Theme: light; reduced motion
- Desktop viewport: 1440 × 900
- Phone viewport: 390 × 844
- Interaction checks: section navigation, product-group loading, proposal dialog,
  JSON edit form, successful PR response, Escape close, and focus restoration
- Result: no browser console errors; Escape restored focus to the invoking button

## Captures

- `desktop-overview.png` — correlated module dashboard and exact source revision
- `desktop-product.png` — product hierarchy browsing and repository edit action
- `desktop-proposal-success.png` — independently reviewable PR success state
- `mobile-overview.png` — single-column dashboard, long status copy, and source data
- `mobile-product.png` — phone group selection and empty document state

## Findings

- Hierarchy, module health, repository revision, and freshness remain visible without
  making every card equally prominent.
- Independent unavailable states do not prevent navigation to healthy modules.
- Desktop uses a persistent group rail; phone uses a linear group list and preserves
  touch-size controls without horizontal scrolling.
- The proposal dialog states the protected-branch behavior before submission and
  provides keyboard trapping, Escape close, and focus restoration.
- Long group names and delivery summaries wrap without clipping in both viewports.

No material visual or keyboard issue remained after the focus-restoration fix.
Production authentication, real GitHub PR creation, and live multi-provider data are
deployment acceptance gates and are not claimed by this synthetic browser review.
