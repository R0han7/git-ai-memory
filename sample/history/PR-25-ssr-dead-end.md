# PR #25: (Reverted) Server-side rendering for the analytics dashboard

## Summary
We tried moving the analytics dashboard to server-side rendering to improve
first paint. This was **reverted**.

## Why it was a dead end
SSR added 400-700ms of server latency per request because the dashboard makes
many aggregation queries that cannot be cached per-user. Client-side rendering
with a skeleton loader tested better on real devices.

## Decision
Do not revisit SSR for the dashboard unless the aggregation queries are
precomputed. This path is closed for now.
