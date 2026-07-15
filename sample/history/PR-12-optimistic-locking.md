# PR #12: Use optimistic locking for order updates

## Summary
We were seeing intermittent deadlocks under load when two workers updated the
same order row. After investigation we decided to use **optimistic locking**
(a `version` column checked on write) instead of `SELECT ... FOR UPDATE` row
locks.

## Rationale
Row locks caused lock-wait timeouts and occasional deadlocks during the
checkout spike. Optimistic locking pushes the conflict to the application layer
where we can retry cleanly.

## Notes
- Convention going forward: all order writes must go through `OrderRepo.save()`
  which enforces the version check.
