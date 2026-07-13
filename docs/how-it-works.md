# How Cursor Chat Storage Works

This is based on local inspection of Cursor's macOS storage layout. Cursor can change this schema, so always run `restore-cursor doctor` and dry runs before editing anything.

## The Important Locations

Per-workspace state:

```text
~/Library/Application Support/Cursor/User/workspaceStorage/<workspaceId>/
```

Global chat state:

```text
~/Library/Application Support/Cursor/User/globalStorage/state.vscdb
```

The global DB is where the important chat index and full message body records can live.

## Terminology

`workspace`

Cursor's internal bucket for a project window or folder identity. The same folder
path can acquire several workspace IDs over time.

`composer`

One conversation container with its own `composerId`. A composer can be a
top-level conversation or a nested subagent session.

`composer header`

The index row that associates a composer with a workspace and stores its title,
timestamps, mode, archive state, and subagent flag. Project inventory counts are
header counts, not necessarily visible-chat counts.

Header timestamps are not reliable evidence of the final message date. Cursor can
keep appending to a long-lived composer while leaving its header timestamp stale.
The project inventory therefore displays header metadata separately from fresher
top-level and all-composer activity timestamps read from `composerData`.

`top-level chat`

A composer whose header is not marked `isSubagent`. This is the closest database
equivalent to a conversation visible in Cursor's chat list. Some top-level headers
are empty placeholders with no bubbles, so even this count can exceed the number
of useful visible conversations.

`subagent session`

A composer spawned by another composer to perform a task such as exploration or
implementation. It has its own ID, header, bubbles, and transcript. Its
`subagentInfo.parentComposerId` points to the parent when that relationship is
available.

`bubble`

One stored event within a composer. A bubble may contain user text, assistant text,
progress text, tool state, or internal metadata. Many tool/event bubbles have no
readable text.

`readable text message`

The term used by `restore-cursor chat` for a bubble whose `text` field is non-empty.
Type `1` is displayed as `USER` and type `2` as `ASSISTANT`. A displayed message is
not guaranteed to equal one complete UI turn: an assistant turn can create several
progress and final-text bubbles. Empty tool bubbles are excluded.

## Key Tables

`composerHeaders`

Stores chat metadata: chat ID, workspace ID, title, created/updated timestamps, archive/subagent flags, and a small JSON header.

A header is not necessarily a top-level chat. Cursor gives subagents and nested
sessions their own composer IDs and marks them with `isSubagent`. Parent composers
can list `subagentComposerIds`, while a child can record its parent in
`subagentInfo.parentComposerId`. Parent and child headers can temporarily belong to
different workspace IDs, especially after Cursor creates a new workspace identity.

`cursorDiskKV`

Stores larger records such as:

- `composerData:<composerId>`
- `bubbleId:<composerId>:<bubbleId>`
- `checkpointId:<composerId>:<checkpointId>`
- `messageRequestContext:<composerId>:<bubbleId>`

`restore-cursor chat <composer-id>` uses these records to show relationship
metadata and bounded readable text. It excludes empty tool bubbles and does not
dump raw internal payloads by default.

`ItemTable`

Stores application-level state such as workspace metadata, feature flags, and sometimes legacy chat/header keys.

## Why Chats Can Look Lost

Cursor does not simply say "show all chats for this folder path." It associates chat headers with an internal `workspaceId`.

That ID can change if a project is opened as:

- a copied folder,
- a renamed folder,
- a symlinked path,
- a Dropbox or CloudStorage path,
- a recovered/restored workspace,
- a nested project root,
- or a newly created Cursor workspace after restart.

When that happens, the old chats can remain in the global DB under the old `workspaceId`, while the current window uses a new `workspaceId`. The UI then looks empty or incomplete.

## How Folder Resolution Works

The path-first commands combine three local signals:

1. Exact normalized folder paths from `workspaceStorage` and workspace metadata.
2. Workspace-directory modification times, used to select the newest exact-path
   workspace as the current target.
3. Normalized Git remote URLs from Cursor's `trackedGitRepos`, used to find history
   associated with renamed or copied folders of the same repository.

The resolver does not use fuzzy folder-name similarity. Same-repository matches are
shown as candidates and always require selection; they are not silently restored.

## What Restore Does

The conservative restore operation changes three workspace-association fields for
each selected composer:

```sql
UPDATE composerHeaders
SET workspaceId = :target,
    value = json_set(value, '$.workspaceIdentifier', json(:target_identity))
WHERE workspaceId = :source;

UPDATE cursorDiskKV
SET value = json_set(value, '$.workspaceIdentifier', json(:target_identity))
WHERE key = 'composerData:' || :composer_id;
```

Cursor filters history using both the indexed header column and the structured
`workspaceIdentifier` embedded in the header and composer data. Moving only the
column can make a selected chat open while leaving the history list empty. The
tool updates only the top-level identity field in composer data. Bubble, checkpoint,
and message-request records remain untouched and keyed by `composerId`.

Selection can be narrower than a whole workspace. `recover-chat` records and moves
one validated top-level composer ID. `--top-level-only --nonempty` selects only
main-chat headers that have at least one stored bubble. In both cases the same SQL
source guard and exact-ID list are used; unselected main chats and all subagents
remain associated with their original workspaces.

Cursor also stores the currently attached/focused composer IDs in the target
workspace's own `state.vscdb`, under `composer.composerData`. A global header move
alone can verify successfully while remaining invisible in the UI. `recover-chat`
therefore prepends the selected ID to `selectedComposerIds` and
`lastFocusedComposerIds`, preserving every existing ID. It snapshots both databases
and updates them in one attached SQLite transaction, so normal SQLite errors roll
back the operation. Cursor uses WAL mode, which does not
guarantee cross-file atomicity if the operating system or machine fails during the
commit itself. Both databases are therefore snapshotted before writing. Logical
undo moves the header back and removes only IDs that the operation itself added.

For each applied restore, the tool writes a versioned JSON manifest containing the
source and target workspace IDs, exact `composerId` values moved, and each selected
chat's original embedded identities. This is why undo can be narrow and exact: it
moves those IDs back only if every indexed and embedded identity still points to
the recorded target.

Before the update, the tool snapshots `state.vscdb` and any existing SQLite WAL/SHM
sidecars. On macOS/APFS it first attempts a copy-on-write clone. A clone reports the
same apparent size as the 17 GB database but initially consumes little additional
physical space; changed blocks can increase that use over time. The `backups`
command reports allocated blocks. APFS sharing means that number is an estimate,
not a guarantee of the incremental space released by deletion.

Managed snapshots live under:

```text
~/Library/Application Support/Cursor/User/globalStorage/
  state.vscdb.restore-cursor-backups/<operation-id>/
```

Deleting a managed snapshot leaves its tiny `manifest.json` in place for audit and
logical undo. Retaining the manifest does not retain a full copy of the chat data.

## Recommended Workflow

1. Run `restore-cursor --version` and `restore-cursor doctor`.
2. Resolve the current project and source candidates.
3. Inspect the source with `--with-bodies` and preview a known transcript.
4. Dry-run one top-level chat as a canary.
5. Quit Cursor, apply the canary, and note its operation ID.
6. Verify while Cursor is closed, then confirm the canary in the real history list.
7. Dry-run and apply the filtered main-chat batch.
8. Verify while Cursor is closed, then inspect several chats in Cursor.
9. Keep the newest emergency snapshots for a chosen retention period.
10. Use managed cleanup so old snapshots do not accumulate.

Do one workspace at a time.
