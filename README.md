# restore-cursor

`restore-cursor` is a small, conservative CLI for inspecting Cursor chat history and re-associating chats with the workspace where you expect them to appear.

Cursor stores chat bodies in a global SQLite database, while visible projects are keyed by internal workspace IDs. If Cursor opens the same repo through a new path, symlink, copied folder, Dropbox path, renamed folder, or recovered session, the chat history can look missing even though the data still exists.

## Safety Model

- Read-only commands use SQLite immutable mode.
- Restore is a dry run unless you pass `--apply`.
- Workspace restores update selected `composerHeaders.workspaceId` values and the
  matching structured `workspaceIdentifier` in both header and composer data.
- `recover-chat` also attaches the selected ID to the target workspace's
  `selectedComposerIds` and `lastFocusedComposerIds`, preserving existing IDs.
- Restore records the exact chat IDs moved in a small operation manifest.
- Restore creates a managed emergency snapshot before writing. `recover-chat`
  snapshots both the global DB and target workspace DB. On APFS it uses a
  copy-on-write clone when available, then falls back to a normal copy.
- Undo moves only the chat IDs recorded by that operation. Existing target chats
  are not affected, and a partial or ambiguous undo is refused.
- Snapshot cleanup retains the small manifest, so logical undo stays available.
- You should fully quit Cursor before running `restore --apply`.

## Install The Command

This project does not need pip or third-party Python packages. The installer keeps
repository source separate from generated installation artifacts:

```bash
cd ~/projects/restore-cursor
./install.sh
```

It copies the runtime package and generates the command outside the repository:

```text
~/.local/share/restore-cursor/    installed Python runtime
~/.local/bin/restore-cursor       generated command
```

`~/.local/bin` must be on `PATH`; it already is on the machine this project was
created for. Test the installed command from any folder:

```bash
restore-cursor doctor
```

Rerun `./install.sh` after changing the source to update the installed runtime.
Use `./uninstall.sh` to remove the generated command and runtime. Both scripts
refuse unsafe paths and avoid replacing or deleting unrelated commands.

Set `RESTORE_CURSOR_PYTHON` only when you need a non-default Python executable.
`RESTORE_CURSOR_INSTALL_ROOT` and `XDG_BIN_HOME` can override installation paths.

## Common Commands

The normal workflow starts with the project folder. From inside the project:

```bash
restore-cursor project .
restore-cursor recover .
```

`project` prints the current target, all exact-path and same-Git-repository history
workspaces, header counts, top-level/subagent counts, header and activity dates,
paths, and full workspace IDs. `recover` shows the
same numbered table and lets you choose a source interactively. It remains a dry
run until `--apply` is supplied.

The date columns intentionally separate Cursor's sometimes-stale header metadata
from fresher composer state:

- `latest_header` is the newest created/updated timestamp stored in a header.
- `main_activity` is the newest activity timestamp for a top-level composer.
- `any_activity` also includes subagent composers.

Activity is a fast, best-effort timestamp from indexed `composerData`; it may differ
by a few seconds from the final readable bubble. Use `chat <id> --tail` when the
exact message text and timestamp matter.

For a non-interactive dry run, select the number shown in that same invocation:

```bash
restore-cursor recover . --source 5
```

You can also identify the source by an old folder path or a unique workspace-ID
prefix:

```bash
restore-cursor recover . --source ~/projects/personal_tools/workbench_agent2
restore-cursor recover . --source 44f993e6
```

After reviewing the dry run, quit Cursor and repeat with `--apply`. The original
hash-based `restore --source ... --target ...` command remains available as an
advanced interface.

For a main-chat-first recovery, preview one known chat as a canary:

```bash
restore-cursor recover-chat . 334112f6
```

`recover-chat` refuses subagents, archived chats, empty session skeletons, and
composers unrelated to the selected project. Its applied form moves exactly one
header, rewrites that chat's structured workspace identity, and attaches the ID to
the target workspace UI state. It leaves all child/subagent headers in place:

```bash
restore-cursor recover-chat . 334112f6 --apply
```

After verifying the canary in Cursor, preview the remaining useful main chats from
the source while excluding subagents and empty headers:

```bash
restore-cursor recover . \
  --source 44f993e6 \
  --top-level-only \
  --nonempty
```

`--nonempty` means at least one stored bubble exists. Both filters affect the exact
ID list recorded in the operation manifest. Each original embedded workspace
identity is also recorded, so verify and undo remain scoped and exact.

Cursor must be fully quit for an applied `recover-chat`. The command snapshots both
databases and updates the global header, embedded identities, and target workspace
state in one attached SQLite transaction. Verify reports header location, embedded
identity, and UI attachment. Cursor
uses WAL mode, so an OS or power failure during the cross-file commit is not given
SQLite's multi-file atomicity guarantee; the two pre-write snapshots cover that
residual failure mode.

Check that the Cursor DB can be read:

```bash
restore-cursor doctor
```

Search chat headers:

```bash
restore-cursor search V3 workbench
```

List all known workspace paths:

```bash
restore-cursor workspaces
```

Inspect a workspace, including body sizes:

```bash
restore-cursor inspect 44f993e64115ff898bc82a01e5c7f798 \
  --with-bodies \
  --sort activity \
  --order newest
```

Without `--with-bodies`, `inspect` shows only header metadata and `header_bytes`.
The body-aware form adds composer-state bytes, total bubbles, readable text-message
counts, and total serialized bubble bytes. Both forms display indexed composer
`activity`. Use `--sort activity --order oldest` to reverse the timeline. With
`--with-bodies`, newest activity is the default sort.

Preview one main chat or subagent by full composer ID or a unique prefix:

```bash
restore-cursor chat 334112f6 --messages 12
restore-cursor chat 3a359f8b --messages 10 --tail
```

The preview labels top-level versus subagent composers, shows parent and direct
child relationships even when they span workspace IDs, and prints only bounded,
readable text messages. Use `--messages 0` for relationship metadata only.

Preview re-associating one workspace's chats to another:

```bash
restore-cursor restore \
  --source 44f993e64115ff898bc82a01e5c7f798 \
  --target 1a8ae8259fbc00c108c9fc21af2b3782 \
  --top-level-only \
  --nonempty
```

Apply the restore after reviewing the dry run and quitting Cursor:

```bash
restore-cursor restore \
  --source 44f993e64115ff898bc82a01e5c7f798 \
  --target 1a8ae8259fbc00c108c9fc21af2b3782 \
  --top-level-only \
  --nonempty \
  --apply
```

The apply command prints an operation ID. After reopening Cursor and checking the
result, verify the exact recorded headers in the database:

```bash
restore-cursor verify <operation-id>
```

Preview an undo at any time:

```bash
restore-cursor undo <operation-id>
```

Quit Cursor and add `--apply` only if you actually want to undo it.

List snapshots and their reported allocated blocks:

```bash
restore-cursor backups
```

After verification, remove one large emergency snapshot while retaining its tiny
manifest and logical undo record:

```bash
restore-cursor delete-backup <operation-id>
restore-cursor delete-backup <operation-id> --apply
```

Preview a retention cleanup that always keeps the two newest snapshots:

```bash
restore-cursor cleanup --older-than 30 --keep-last 2
restore-cursor cleanup --older-than 30 --keep-last 2 --apply
```

If macOS does not allow the process check, quit Cursor yourself and add
`--confirm-cursor-quit` to an applied `restore` or `undo`. This flag does not
override a positive detection: if Cursor is found running, the command still stops.

## Your Current Recovery Clues

From the July 13, 2026 investigation:

- Large V3 history: `44f993e64115ff898bc82a01e5c7f798`
- Recent July `workbench_agent` history: `7a3930738e9c287d549ba7fff16d8c03`
- New/current `workbench_agent` workspace: `1a8ae8259fbc00c108c9fc21af2b3782`

Use dry runs first. Restore one canary, verify it while Cursor is closed, confirm it
in the real history list, and only then continue to a batch.

Documentation:

- [AI-assisted recovery guide](docs/ai-assisted-recovery.md)
- [Worked `workbench_agent` recovery example](docs/workbench-agent-workflow.md)
- [General restore playbook](docs/restore-playbook.md)
- [Cursor storage details](docs/how-it-works.md)
