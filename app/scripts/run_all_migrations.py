"""Run every migration script, in dependency order.

There is no migration framework here and no ledger recording what has already
run — `app/scripts/` is a set of one-off scripts written for the March→July
refactors. This runner exists so "bring a database up to date" is one command
instead of sixteen invocations in an order you have to reconstruct from
docstrings.

**Dry-run by default.** Nothing is written without `--apply`, matching every
individual script. Run it dry first and read the per-step output.

    python -m app.scripts.run_all_migrations              # dry run
    python -m app.scripts.run_all_migrations --apply      # write

Three scripts have no dry-run mode and write the moment they are invoked. They
are skipped entirely unless `--apply` is given, and are listed explicitly in the
dry-run output so their absence is visible rather than silent.

Ordering comes from the scripts' own stated constraints — "must run before the
reader cutover", "must run after messages have real conversation ids" — and from
the order of the archived openspec changes that introduced them. Where a script
states no constraint it is placed with the change that added it.

Each step is idempotent by its own documentation, so re-running a database that
is already current should report no work. That is the only way to discover what
has already been applied, since nothing records it.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class Step:
    module: str
    why: str
    #: False for scripts that write immediately with no dry-run flag.
    supports_dry_run: bool = True


#: Dependency order. Do not reorder without reading the docstrings — several
#: steps are destructive only when their predecessor has already run.
STEPS: tuple[Step, ...] = (
    # --- foundational: field shapes everything else queries on ---------------
    Step(
        "migrate_user_ids_to_str",
        "ObjectId -> str for messages.sender_id/receiver_id and refresh_tokens."
        " Everything downstream queries these as strings.",
        supports_dry_run=False,
    ),
    Step(
        "backfill_usernames",
        "Users predating usernames get one generated.",
        supports_dry_run=False,
    ),
    Step(
        "migrate_messages_media",
        "Legacy per-type media fields folded into the media shape.",
        supports_dry_run=False,
    ),

    # --- first-class conversations (2026-07-21 → 07-23) ----------------------
    Step(
        "backfill_conversations",
        "Materialize conversation + participant docs from historical messages."
        " Must precede any step that rewrites message conversation ids.",
    ),
    Step(
        "finalize_conversation_model",
        "Backfill derived conversation/participant fields (visibility,"
        " posting_policy, notification_level, member_count).",
    ),

    # --- conversation cutover: its own orchestrator, five ordered steps ------
    Step(
        "run_conversation_cutover_migrations",
        "Chains call-content repair -> conversation message ids -> receipts ->"
        " drop legacy indexes -> legacy field cleanup, in that required order.",
    ),

    # --- the unified refactor (2026-07-27) -----------------------------------
    Step(
        "migrate_message_containers",
        "Re-address messages by container_type/container_id. Must run before the"
        " unified-messages reader cutover: un-backfilled rows are invisible.",
    ),
    Step(
        "migrate_relationships",
        "Fold pings, participants, space members and join requests into"
        " relationships. Must precede resource roles, which fills role_ids.",
    ),
    Step(
        "migrate_resource_roles",
        "Seed roles and point Relationship.role_ids at them. Must run before the"
        " AuthorizationService cutover, or can() resolves no roles.",
    ),
    Step(
        "migrate_channels_and_profile_feed",
        "Convert legacy channel conversations into first-class channels.",
    ),
    Step(
        "migrate_dependent_models",
        "Reshape satellite documents; removes legacy fields after deriving"
        " replacements, so this is the least reversible step. Runs last.",
    ),
)


def run(step: Step, *, apply: bool, extra: list[str]) -> int:
    cmd = [sys.executable, "-m", f"app.scripts.{step.module}"]
    if apply and step.supports_dry_run:
        cmd.append("--apply")
    cmd.extend(extra)

    print(f"\n{'=' * 72}\n{step.module}\n  {step.why}\n  $ {' '.join(cmd)}\n{'=' * 72}")
    return subprocess.call(cmd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write changes. Without this flag every step runs as a dry run and "
        "the three scripts that cannot dry-run are skipped.",
    )
    parser.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Keep going after a failing step. Off by default: a later migration "
        "usually assumes its predecessor succeeded.",
    )
    parser.add_argument(
        "--only",
        metavar="MODULE",
        help="Run a single step by module name, with the same flags.",
    )
    args, extra = parser.parse_known_args()

    steps = STEPS
    if args.only:
        steps = tuple(s for s in STEPS if s.module == args.only)
        if not steps:
            print(f"no step named {args.only!r}; known: {', '.join(s.module for s in STEPS)}")
            return 2

    if not args.apply:
        print("DRY RUN — nothing will be written. Re-run with --apply to commit.")
        skipped = [s.module for s in steps if not s.supports_dry_run]
        if skipped:
            print(
                "\nThese have no dry-run mode and are SKIPPED here; they will run "
                "under --apply:\n  " + "\n  ".join(skipped)
            )

    failures: list[str] = []
    ran = 0

    for step in steps:
        if not args.apply and not step.supports_dry_run:
            continue
        code = run(step, apply=args.apply, extra=extra)
        ran += 1
        if code != 0:
            failures.append(f"{step.module} (exit {code})")
            if not args.continue_on_error:
                print(f"\nSTOPPED at {step.module}. Later steps assume it succeeded.")
                break

    print(f"\n{'=' * 72}")
    print(f"{'applied' if args.apply else 'dry run'}: {ran} step(s) executed")
    if failures:
        print("failed: " + ", ".join(failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
