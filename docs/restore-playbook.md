# Restore Playbook

Use this playbook when a Cursor project opens but its chat history appears missing.

For the real `workbench_agent` inventory, exact commands, backup paths, and a
cross-workspace risk analysis, see
[Worked Example: Recovering `workbench_agent`](workbench-agent-workflow.md).
For a shared-control process where an AI agent advises but the user runs every
write, see [AI-Assisted Recovery Guide](ai-assisted-recovery.md).

Install the dependency-free command once:

```bash
cd ~/projects/restore-cursor
./install.sh
```

This installs a private runtime in `~/.local/share/restore-cursor` and generates
the command in `~/.local/bin`. It does not modify Homebrew Python or require pip.

## 1. Start From The Current Project Folder

```bash
cd /path/to/project
restore-cursor project .
restore-cursor recover .
```

The tool resolves the current target from the newest Cursor workspace entry for
that exact path. It finds older exact-path entries and renamed/copied folders that
share the same Git repository. It shows their header, top-level, and subagent
counts plus full IDs, then lets you choose a source by number.

`recover` is still a dry run unless `--apply` is supplied. You may also select an
old folder directly:

```bash
restore-cursor recover . --source /path/to/old/project-copy
```

## 2. Search Chat Titles If Needed

```bash
restore-cursor search V3
restore-cursor search workbench
restore-cursor search "storage polish"
```

The search command prints composer IDs, workspace IDs, names, and timestamps.

## 3. Confirm The Source Has Real Body Data

```bash
restore-cursor inspect <source-workspace-id> --with-bodies
```

Large `bubble_bytes` values mean the chat bodies are present.

## 4. Identify The Target Workspace

The target is the workspace ID Cursor is currently using for the folder you are opening.

If you are unsure, run:

```bash
restore-cursor workspaces | grep -i workbench
```

## 5. Dry Run

Prefer a known, non-empty main chat as a one-header canary:

```bash
restore-cursor recover-chat . <composer-id>
```

For a main-chat batch that leaves subagents and empty skeletons untouched:

```bash
restore-cursor recover . \
  --source <old-id> \
  --top-level-only \
  --nonempty
```

The whole-workspace advanced form remains available when that is genuinely the
desired selection:

```bash
restore-cursor restore --source <old-id> --target <new-id>
```

Read the total source count, selected count, selection description, and sample
titles. Every form is still read-only without `--apply`.

## 6. Apply

Quit Cursor completely, then:

```bash
restore-cursor recover-chat . <composer-id> --apply
```

The command refuses to continue if it detects Cursor running. It creates a managed
operation under `state.vscdb.restore-cursor-backups/`, snapshots every database it
will change, and records every chat ID plus its original embedded workspace
identities. For `recover-chat`, that means the global DB plus the target workspace
DB and its composer-selection state. Save the operation ID printed by the command.
After the canary is verified, apply the filtered batch as a separate operation.

If macOS denies the automatic process check, first confirm Cursor is fully quit,
then repeat with `--confirm-cursor-quit`. The flag cannot override a detected Cursor
process. Restore also refuses an unknown target workspace ID by default, which
protects against a typo that would hide the chats again.

## 7. Verify

Keep Cursor closed and verify every recorded header and embedded identity is now
associated with the target:

```bash
restore-cursor verify <operation-id>
```

Only after verification succeeds, open Cursor on the project. Confirm the chat is
present in the history list and that its transcript opens. An already-open chat
without a history entry is not a successful canary.

Do not clean up its emergency snapshot unless this reports `Verification: OK` and
the expected chats are visible in Cursor.

## 8. Undo If Needed

First preview the exact operation:

```bash
restore-cursor undo <operation-id>
```

The preview must show every recorded chat at the target. To reverse it, quit Cursor:

```bash
restore-cursor undo <operation-id> --apply
```

Undo changes only the chat IDs recorded in that restore. It refuses partial undo
if any recorded header is missing or has since moved elsewhere.

## 9. Manage Snapshot Storage

List managed operations and allocated blocks reported by the filesystem:

```bash
restore-cursor backups
```

APFS copy-on-write clones can each appear as large as the live database while
sharing most physical blocks. The listed `disk_bytes` values and `du` output are
not additive measures of unique storage. Record `df -h /Users/$USER` before and
after deletion when actual reclaimed space matters. Clone overhead grows as the
live database diverges from the retained snapshot.

Once the restored chats have been checked in Cursor, preview and delete a specific
emergency snapshot:

```bash
restore-cursor delete-backup <operation-id>
restore-cursor delete-backup <operation-id> --apply
```

This deletes only the large snapshot files. It retains `manifest.json`, which is
small and still supports logical undo.

Use this default retention policy:

1. Keep two snapshots during active recovery: the canary and batch checkpoints.
2. Once both operations and the real UI are verified, remove failed, undone, and
   superseded snapshots manually, one operation at a time.
3. Keep the newest successful snapshot for 14 days as an emergency fallback.
4. After 14 stable days, preview and manually delete that final snapshot if a full
   rollback is no longer worth its growing storage cost.
5. Retain manifests indefinitely; they are small and preserve audit and logical
   undo metadata.

For routine cleanup, preview first while preserving the newest snapshot:

```bash
restore-cursor cleanup --older-than 14 --keep-last 1
restore-cursor cleanup --older-than 14 --keep-last 1 --apply
```

Cleanup never deletes incomplete or failed-operation snapshots automatically. It
also keeps the newest requested number of snapshots regardless of age. During an
active recovery, use `--keep-last 2`. The final retained snapshot must be deleted
with an explicit `delete-backup` command when its retention period ends.

## Recovery Layers

1. Logical undo is the normal recovery path. It is precise and does not erase new
   Cursor activity created after the restore.
2. The full database snapshot is emergency insurance for unexpected schema or
   database damage. Replacing the live DB from it can erase newer Cursor activity,
   so that should be a deliberate manual recovery step.
3. After deleting a snapshot, logical undo remains, but full-database rollback for
   that operation no longer does.
