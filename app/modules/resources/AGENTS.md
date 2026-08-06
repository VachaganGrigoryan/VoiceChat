# modules/resources/

**Ordering here is the design, not an implementation detail.**

Nothing in this codebase opens a Mongo session or calls `with_transaction`. A
cascade spanning ten collections therefore *cannot* be atomic, and the ordering
is the only thing standing between a failed delete and unrecoverable data.

Two rules, both load-bearing:

**1. Snapshot the realtime audience before deleting anything.** Both
`participant_ids` and `affected_viewer_ids` are derived from `relationships`,
which the cascade is about to empty. Read them afterwards and the deletion event
goes to nobody.

**2. Children before parents; the parent record last.** An interruption then
leaves a resource that still exists, still resolves an owner, and can simply be
deleted again. The opposite order strands children under a missing parent — and
a space-owned channel or group whose space is gone resolves no owner at all, so
nobody can manage or delete it, ever. That is the one failure the ownership
model cannot recover from, which is why a space cascade is not optional.

## What must be cleared

A miss here is invisible: the delete still returns 204 and the orphans surface
much later. `app/tests/unit/test_resource_cascade.py` asserts *which collections
are targeted* for exactly this reason;
`app/tests/integration/test_hard_delete_cascade.py` then proves the rows are
actually gone against a real database.

Two traps when extending it:

- **`ParticipantDocument`, `SpaceMemberDocument` and `JoinRequestDocument` back
  no collections.** They are views over `relationships` (see
  `modules/relationships/compat.py`). Cascading them is a no-op at best.
- **Some records key off the deleted message ids, not the container** — saved
  messages, polls, message-scoped notifications. They can only be found while
  those ids are still in hand, so collect them before deleting the messages.

Storage failures are swallowed deliberately: an unreferenced blob is waste, a
half-deleted resource is not recoverable. The document deletions win.
