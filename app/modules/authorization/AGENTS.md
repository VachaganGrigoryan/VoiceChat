# modules/authorization/

**One decision point.** `AuthorizationService.can()` is the only place that
decides whether an action is permitted. Everything else asks it. If you are
about to write an `if user.role == ...` anywhere, that is the bug.

## Resolution order

The order in `can()` is deliberate; each step exists because an earlier design
got it wrong:

1. user exists
2. own-scoped authorship — `.own` permissions resolve against `sender_id`, and
   their `.any` sibling also satisfies them (`ANY_EQUIVALENT`)
3. **owner bypass** — see below
4. active membership
5. deny override, then allow override (`permission_overrides` on the relationship)
6. roles on the resource, unioned with roles inherited from the parent space
7. `_policy_restricts` caps what a grant can reach
8. `_policy_grants`
9. public visibility
10. default deny

## Ownership is not a role

There is no Owner role and there must not be one. Ownership is a **bypass**
resolved from the resource's owner, and it cascades: `OwnershipResolver` resolves
a space-owned channel or group by recursing into the space, so a space owner is
the effective owner of everything the space owns without holding any membership
there.

`PLATFORM_SAFETY_ACTIONS` is currently empty, which means an owner bypasses
**every** action including delete. If you ever need an action an owner must not
be able to take, that set is the lever — not a special case in `can()`.

The corollary matters for deletion: a child whose space is gone resolves *no*
owner, so nobody can ever manage or delete it again. That is why
`modules/resources/cascade.py` must never orphan a child.

## Permissions are data

Dotted strings in `permissions.py`, never boolean columns — so a role is data
and a new capability needs no schema change. Roles live in the `roles`
collection scoped by `(scope_type, scope_id)`; `SPACE_ONLY_PERMISSIONS` is
stripped for non-space scopes.

`channel.delete` means "may delete channels **within this space**". It is not
the per-channel delete gate; that is `resource.delete` scoped to the channel.

## Gating happens in services

Routers carry only `require_verified_user` and `rate_limit`. They never call
`require()`. Match that split — a route that gates itself is inconsistent with
every other route here and will be missed by anyone reading the service.
