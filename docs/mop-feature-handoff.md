# mop Feature Handoff

Scoped down from the broader backup/B2 investigation
(`docs/backblaze-cleanup-plan.md` + the treasurebox-repo findings it links to).
Everything infra/pipeline-shaped (restic, corebox, rclone, ansible) belongs to
`~/src/treasurebox`, not here. This doc is the opposite filter: **only what mop
itself needs to grow** to execute that cleanup plan. Confirmed: no
sync/upload/delete commands are needed — local hygiene is `find`/`mv`, B2
mutation is the existing `hide`. The gap was smaller than that, and it's now
closed.

## Implemented: safety flags on `b2.py hide`

`docs/backblaze-cleanup-plan.md` Phase 4b calls `./b2.py hide --dry-run ...`
and `./b2.py hide --log hidden.log ...` — both now exist (`b2.py:140-191`),
covered by `tests/test_b2.py::TestHide`.

1. **`--dry-run`** — prints `<path> would hide` per line, skips
   `bucket.hide_file()` entirely. No count summary printed in this mode (there's
   nothing to count yet).
2. **`--log PATH`** — appends `<path>\thidden\n` for each successfully-hidden
   file. Opt-in; no default path, so `hide` never silently writes anywhere
   unexpected.
3. **Count summary** — `Total: N hidden, N not found, N already hidden` printed
   at the end of a non-dry-run.

Resolved: `hidden.files` (the empty placeholder) is gone — it was never wired
to anything and `--log` doesn't default to it. `.gitignore` now excludes
`hidden.files`/`hidden.log`/`*.db/`/the bucket-named local index dirs so a real
cleanup run never risks committing personal data or B2 index state.

## Not needed (ruled out, don't re-litigate)

- `local.py`/`b2.py` sync or upload command — cleanup is local-first; B2 only
  ever needs `hide`, never a write-back.
- Local delete/trash command in `local.py` — plain `mv` to `.trash/` is
  sufficient (see cleanup plan Phase 3).
- `size` field in `local.py index` — zero-byte detection uses `find -size 0`
  on the filesystem instead; adding `size` to local metadata would duplicate
  data the filesystem already tracks for free.

## Known unrelated issue (not this doc's scope, flagging so it isn't lost)

`tests/test_leveldb.py::TestCliCommands::test_ls_command` fails on current
`main` — `leveldb.py ls` invoked with just a dbpath (no wildcard) exits 2
despite `wildcard` being declared `@click.argument('wildcard', default=None)`.
Pre-existing, unrelated to the `hide` work above; worth its own fix.
