# Authorization

## Purpose

Every "may this user do this?" question in the backend is answered by one method: `AuthorizationService.can()` in `app/modules/authorization/service.py`. Services call `require()`, which raises when `can()` says no. Routers only check that the user is verified and apply rate limits, and they never gate on permissions.

The design rationale, including what earlier designs got wrong, is in `app/modules/authorization/AGENTS.md`.

## Permissions

Permissions are dotted strings defined in `app/modules/authorization/permissions.py`. They are data rather than boolean columns, so adding one needs no schema change.

| Area | Permissions |
|---|---|
| Resource | `resource.view`, `resource.manage`, `resource.delete` |
| Members | `member.view`, `member.invite`, `member.approve`, `member.remove`, `member.manage` |
| Roles | `role.view`, `role.manage` |
| Messages | `message.read`, `message.create`, `message.edit.own`, `message.edit.any`, `message.delete.own`, `message.delete.any`, `message.pin` |
| Threads | `thread.create`, `thread.reply` |
| Reactions | `reaction.create`, `reaction.delete.own`, `reaction.delete.any` |
| Space scope | `channel.create`, `channel.manage`, `channel.delete`, `group.create`, `group.manage` |
| Calls | `call.create`, `call.manage` |
| Polls | `poll.create`, `poll.manage` |

- `.own` permissions are checked against the message author, and the matching `.any` permission also satisfies them.
- `channel.delete` means "may delete channels **within this space**". Deleting a single channel is gated by `resource.delete` on that channel.

## Resolution order

`can()` checks these steps in order. The first one that decides wins.

1. The user exists.
2. **Authorship**: `.own` actions resolve against the message `sender_id`.
3. **Blocks**: a block denies every action except reads.
4. **Owner bypass**: the resource owner may do anything.
5. Active membership.
6. Per-membership **deny** override, then **allow** override (`permission_overrides` on the relationship).
7. **Roles** on the resource, unioned with roles inherited from the parent space.
8. Policy restrictions, which cap what a grant can reach (for example a channel's `posting_policy`).
9. Policy grants.
10. Public visibility.
11. Default deny.

### Ownership is a bypass, not a role

There is no Owner role. Ownership is resolved from the resource's owner, and it **cascades**: a space owner is the effective owner of every channel and group the space owns, without holding a membership in each one. For the same reason a child resource must never outlive its space, since it would then resolve no owner at all. See `app/modules/resources/cascade.py`.

## Roles

Roles are named permission sets stored in the `roles` collection and scoped to one space, channel or conversation. They attach to **memberships**, not to users.

- `GET /spaces/{resource_id}/roles`, `POST /spaces/{resource_id}/roles`
- `PATCH /spaces/{resource_id}/roles/{role_id}`, `DELETE /spaces/{resource_id}/roles/{role_id}`
- `GET /channels/{resource_id}/roles`, `POST /channels/{resource_id}/roles`
- `PATCH /channels/{resource_id}/roles/{role_id}`, `DELETE /channels/{resource_id}/roles/{role_id}`
- `GET /conversations/{resource_id}/roles`, `POST /conversations/{resource_id}/roles`
- `PATCH /conversations/{resource_id}/roles/{role_id}`, `DELETE /conversations/{resource_id}/roles/{role_id}`
- `PUT /relationships/{relationship_id}/roles`
  Replace the roles on one membership. Body: `role_ids` (up to 16).

A role body has `name` (1–64 characters), `permissions` (up to 64 strings) and `priority`. Space-only permissions are stripped from roles scoped to a channel or conversation.

## Capabilities

Clients do not re-implement these rules. They ask the server what the current viewer may do, and render only the actions that come back.

- `POST /viewer/capabilities`
  Batch query. Body: `resources` (1–50 `{ "type": "space" | "conversation" | "channel", "id": "…" }`) and optional `actions` (up to 64). POST because the batch does not fit a query string.
- `GET /spaces/{resource_id}/capabilities`
- `GET /channels/{resource_id}/capabilities`
- `GET /conversations/{resource_id}/capabilities`

Each result contains:

- `allowed` and `denied`: action strings. Both lists are explicit, so a client can tell "refused" apart from "not evaluated".
- `standing`: `is_owner`, `membership_status`, `is_follower`, `role_ids`. This describes the viewer's position and is not a permission decision.
- `policy`: the resource's policy fields, for management forms to display. **Never gate on `policy`**; gate on `allowed`.

## Socket Events

- `capabilities.invalidated`
  Sent to affected users when a change such as a role edit, a membership change or a policy update may have changed what they can do. Payload: `{ "resource": { "type", "id" } }`. Clients should refetch capabilities for that resource.

## Notes

- A granted `message.edit.own` means "you may edit messages you wrote". The client still has to compare `message.sender_id` before offering the action on a specific message.
- The web client deliberately leaves `resource.delete` out of its default capability batch and requests it explicitly where a delete affordance can appear.
