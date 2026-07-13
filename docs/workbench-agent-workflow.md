# Worked Example: Recovering `workbench_agent`

This document uses the real workspace inventory found on July 13, 2026. It restores
one source at a time into the Cursor workspace currently associated with:

```text
/Users/amir/projects/personal_tools/workbench_agent
```

No command in the preview stages changes Cursor data or creates a backup. A managed
backup is created automatically only when an `--apply` command is run.

## What Chat, Composer, Header, Bubble, And Message Mean

These words describe different levels of Cursor's storage:

| Term | Meaning in this workflow |
|---|---|
| Workspace | Cursor's internal project bucket. One folder path can have several workspace IDs. |
| Composer | One conversation container identified by a `composerId`. It can be top-level or a subagent. |
| Header | The index row that assigns a composer to a workspace and stores title, timestamps, and flags. |
| Top-level chat | A composer not marked `isSubagent`; normally the closest match to a visible Cursor conversation. |
| Subagent session | A nested composer spawned by a parent composer for a specific task. |
| Bubble | One stored event inside a composer, including text, progress, tools, or internal state. |
| Readable text message | A bubble with non-empty text, shown by `restore-cursor chat`. |

The `headers` column in `restore-cursor project .` counts top-level and subagent
composer headers together. It does not mean “this many chats will appear in the
Cursor sidebar.” The adjacent `top_level` and `subagents` columns explain the
composition of that total.

The three date columns answer different questions:

| Column | Meaning |
|---|---|
| `latest_header` | Newest created/updated timestamp in the small header metadata. Cursor may leave this stale. |
| `main_activity` | Newest activity timestamp in `composerData` for a top-level composer. This is the strongest quick signal for a recently used main chat. |
| `any_activity` | Newest activity timestamp across top-level and subagent composers. |

These activity dates are fast, best-effort signals from indexed composer state;
they can differ from the final readable bubble by a few seconds. For the long-lived
`334112f6...` main composer, the header still looks old while `main_activity`
reaches July 13. The recent 13-header source has fresh `any_activity` because its
subagents ran recently, but its top-level headers are empty session skeletons.
Use `restore-cursor chat <id> --tail` to verify exact message timestamps and text.

The `chat` command accepts any composer ID, despite its user-friendly name. It
labels the selected composer as `top-level` or `subagent`, shows parent and direct
children, and then prints a bounded transcript.

A bubble is finer-grained than a conversational turn. Type `1` text bubbles are
shown as `USER`; type `2` text bubbles are shown as `ASSISTANT`. Type `2` can include
progress updates as well as final answers, and many additional type `2` tool/event
bubbles contain no text and are filtered out. Therefore:

```text
bubble count >= readable text message count
readable text message count is not a reliable visible-turn count
```

For the main `334112f6...` composer, the database currently has 7,488 bubbles but
only 1,326 readable text messages. For the `3a359f8b...` subagent, it has 197
bubbles but only 3 readable text messages. This is expected and does not indicate
missing content by itself.

## Known Workspaces

The important entries in the current inventory are:

| Role | Workspace ID | Headers | Path |
|---|---|---:|---|
| Current target | `1a8ae8259fbc00c108c9fc21af2b3782` | 2 | `~/projects/personal_tools/workbench_agent` |
| Recent July source | `7a3930738e9c287d549ba7fff16d8c03` | 13 | `~/projects/personal_tools/workbench_agent` |
| Older exact-path source | `f02b6407ee777d5070d1b5d6b9392721` | 2 | `~/projects/personal_tools/workbench_agent` |
| Older exact-path source | `5f6de3e7cccee289ba18a4d2e6880a0b` | 2 | `~/projects/personal_tools/workbench_agent` |
| History-safe copy | `a5252b0bba25fb516ae58f8a457d29bd` | 2 | `~/workbench_agent2-HISTORY-SAFE` |
| Large V3 source | `44f993e64115ff898bc82a01e5c7f798` | 86 | `~/projects/personal_tools/workbench_agent2` |

The counts are database header counts. Some headers can be archived chats or
subagent sessions, so Cursor may show fewer top-level conversations than the raw
count.

In this inventory, the V3 source contains 68 top-level headers and 18 subagent
headers. Of those 68 top-level headers, 67 have stored bubbles. The recent July
source contains 3 top-level headers with no message bubbles and 10 subagent headers
with real transcripts. Eight of those July subagents point back to the V3 top-level
composer `334112f6...`.

This workflow deliberately restores main chats first. It accepts that subagent
headers may remain outside the target because the visible main transcripts are the
priority. The parent and child bodies remain stored globally by composer ID, and no
subagent data is deleted.

## Step 1: Enter The Current Project

```bash
cd ~/projects/personal_tools/workbench_agent
```

Running from the current project lets `recover .` resolve the target workspace from
the exact folder path. The newest `workspaceStorage` entry for that path is selected
as the target.

## Step 2: Check The Database

```bash
restore-cursor --version
restore-cursor doctor
```

This worked recovery used `restore-cursor 0.7.0`. `doctor` opens the database in
read-only immutable mode and checks the expected schema. Neither command creates a
backup or modifies Cursor.

## Step 3: Resolve The Project Inventory

```bash
restore-cursor project .
```

This is also read-only. It combines exact folder paths with Cursor's recorded Git
repository identity. It shows the current target and possible sources, but selects
nothing automatically. Compare `main_activity` before `any_activity` when the
recovery priority is visible main chats rather than subagent transcripts.

Sources sharing the current path cannot be selected by path because that path is
ambiguous. Use their option number during an interactive run or, preferably, their
stable workspace-ID prefix such as `7a393073`.

## Step 4: Inspect The V3 Source Bodies

```bash
restore-cursor inspect 44f993e64115ff898bc82a01e5c7f798 \
  --with-bodies \
  --sort activity \
  --order newest
```

This confirms that the source has `composerData` and message bubble records, not
only titles. Large `bubble_bytes` values are evidence that the message bodies are
still present. `text_messages` counts bubbles with non-empty text. This remains
read-only and creates no backup.

The `activity` column comes from fresher indexed composer state, while `created`
and `updated` come from the potentially stale small header. The command above sorts
newest activity first; use `--order oldest` to reverse it.

Plain `inspect` reports `header_bytes`, which measures the small index header.
Use `--with-bodies` when deciding whether a composer has meaningful content. A
small `composer_bytes` value with zero `bubbles` and zero `text_messages` is an
empty session skeleton, not a recovered transcript.

Preview the large parent chat and its direct subagents:

```bash
restore-cursor chat 334112f6 --messages 12 --tail
```

Preview the last ten readable messages from one July subagent:

```bash
restore-cursor chat 3a359f8b --messages 10 --tail
```

The `created` and `updated` columns from `inspect --with-bodies` come from the small
composer header and can remain stale even while new bubbles are appended. That
table proves body presence and size; it is not an exact activity timeline. The
first `chat --tail` command reads bubble timestamps and should show the July 13
messages in `334112f6...`. It also identifies that composer as the top-level “Code
and documentation review” chat and lists its direct children. The second command
identifies `3a359f8b...` as the “Audit DB OOM hot paths” subagent and shows that its
parent is `334112f6...` in a different workspace. These commands are read-only.

## Step 5: Preview One Main Chat As A Canary

```bash
restore-cursor recover-chat . 334112f6
```

The preview must show:

```text
Composer: 334112f6-df29-412a-8a9d-6b769d3f99b2
Title: Code and documentation review
Source: 44f993e64115ff898bc82a01e5c7f798
Target: 1a8ae8259fbc00c108c9fc21af2b3782
Archived: False
Bubbles: 7,488
Readable text messages: 1,326
Selection: exactly 1 top-level composer header; child headers stay in place
Target workspace UI state: .../workspaceStorage/1a8ae825.../state.vscdb
UI attachment: selectedComposerIds and lastFocusedComposerIds
Embedded workspace identity: header and composer data will follow target
```

The command refuses a subagent, archived chat, empty session skeleton, unrelated
workspace, or chat already at the target. Without `--apply`, it creates neither a
manifest nor a backup and changes nothing.

## Step 6: Quit Cursor Completely

Use `Cmd+Q`, not only window close. The apply command checks for Cursor processes
and refuses to write if it detects one.

If macOS prevents the process check, confirm Cursor is fully quit and add
`--confirm-cursor-quit`. That flag cannot override a positively detected Cursor
process.

## Step 7: Apply The One-Header Canary

```bash
restore-cursor recover-chat . 334112f6 --apply
```

The successful write should report one update at every layer:

```text
Updated headers: 1
Updated embedded header identities: 1
Updated embedded composer data identities: 1
Attached composers to target workspace UI state: 1
```

The operation proceeds in this order:

1. Re-resolve the project and exact composer ID.
2. Repeat all main-chat safety checks.
3. Refuse to continue if Cursor is detected running.
4. Create a versioned operation manifest.
5. Snapshot the global database and any SQLite WAL/SHM sidecars.
6. Refuse the update if the snapshot fails.
7. In one attached SQLite transaction, change exactly that header's indexed
   workspace ID, its embedded header identity, its embedded composer-data
   identity, and prepend its ID to the target's selected/focused arrays.
8. Mark the operation `applied` and print its operation ID.

The managed operation is stored at:

```text
~/Library/Application Support/Cursor/User/globalStorage/
  state.vscdb.restore-cursor-backups/<operation-id>/
```

Its normal contents are:

```text
manifest.json
state.vscdb.bak
state.vscdb-wal.bak    # only if a WAL file existed
state.vscdb-shm.bak    # only if an SHM file existed
workspace-state.vscdb.bak
workspace-state.vscdb-wal.bak  # only if a WAL file existed
workspace-state.vscdb-shm.bak  # only if an SHM file existed
```

`manifest.json` records the exact composer IDs, source, target, timestamps, status,
snapshot method, original embedded identities, and exact target composer-state
additions. On APFS, the database backups normally begin as fast copy-on-write
clones. If cloning is unavailable, the tool checks free space before making a full
copy.

Cursor stores both databases in WAL mode. The attached transaction rolls back both
updates for normal SQLite or process errors, but SQLite does not promise multi-file
atomicity if macOS or the machine fails during the commit itself. The pre-write
snapshots of both files are the emergency protection for that narrow failure mode.

### Recovery From The Pre-0.6 Canary

Version 0.5.1 moved the global header but did not attach the composer to the target
workspace UI. Its database verification passed, but the chat remained invisible.
Do not continue to the batch from that state. With Cursor fully quit, undo it:

```bash
restore-cursor undo 20260713T173515Z-2e13f718
restore-cursor undo 20260713T173515Z-2e13f718 --apply
restore-cursor verify 20260713T173515Z-2e13f718
```

The final verification should report the header back at the source. Then rerun
Steps 5 through 8 using the current installed version.

### Recovery From The Pre-0.7 Empty-History Batch

Version 0.6.1 updated the indexed header workspace but left the structured
`workspaceIdentifier` inside the header and composer data pointing at
`workbench_agent2`. The selected canary could open because it was explicitly
attached, but the remaining history list stayed empty. The messages were not
deleted.

For this working example, fully quit Cursor and undo the batch first, followed by
the old canary:

```bash
restore-cursor undo 20260713T180349Z-175fcd30 --apply
restore-cursor verify 20260713T180349Z-175fcd30

restore-cursor undo 20260713T175908Z-1d8124aa --apply
restore-cursor verify 20260713T175908Z-1d8124aa
```

Both verifications should report the recorded headers at the source. Version 0.7
then reruns the canary and batch while changing and verifying all three workspace
associations. Keep the old snapshots until the corrected recovery is visible and
verified.

## Step 8: Verify And Check Cursor

Keep Cursor closed and verify the operation first:

```bash
restore-cursor verify <operation-id>
```

The database-level success condition is that the one recorded header now points at
the target, both embedded identity counts are complete, and verification reports
both `Workspace UI selected: True` and `Workspace UI focused: True`. The target
will have 3 headers: its original 2 plus the recovered main chat.

Then reopen Cursor on `workbench_agent`. Confirm that “Code and documentation
review” appears as an entry in the history list, not merely as an already-open
chat, and that its recent transcript is readable before continuing. This distinction
caught the pre-0.7 incomplete restore.

If Cursor and the transcript both look healthy, the canary succeeded. Keep its
emergency snapshot until the main-chat batch is also applied and verified. This
temporarily leaves two snapshots, but preserves both the full pre-canary state and
the full pre-batch state during the recovery. Clean them up only in Step 12.

## Step 9: Preview The Remaining Main Chats

```bash
restore-cursor recover . \
  --source 44f993e6 \
  --top-level-only \
  --nonempty
```

Before the canary is applied, this selects 67 headers. After the canary succeeds,
it selects the remaining 66 automatically because `334112f6...` no longer belongs
to the source. The applied-state preview should show:

```text
Source headers: 85
Selected headers: 66
Selection: top-level only, stored bubbles required
Existing target headers: 3
Target is a known Cursor workspace: True
```

The 18 V3 subagents and one empty top-level skeleton are excluded.

## Step 10: Apply And Verify The Main-Chat Batch

Quit Cursor, then run:

```bash
restore-cursor recover . \
  --source 44f993e6 \
  --top-level-only \
  --nonempty \
  --apply
```

The successful batch write should report 66 updates at every global layer:

```text
Updated headers: 66
Updated embedded header identities: 66
Updated embedded composer data identities: 66
```

This creates a new snapshot and a separate manifest containing exactly the 66
selected composer IDs. Keep Cursor closed and verify first:

```bash
restore-cursor verify <batch-operation-id>
```

The expected identity checks are:

```text
At target: 66
At source: 0
Embedded header identity: 66/66
Embedded composer data identity: 66/66
Verification: OK
```

Then reopen Cursor, confirm that the history list contains the recovered chats,
and open several old and recent main transcripts.

After both operations, the target should contain its original 2 headers plus 67
recovered non-empty main chats, for 69 total. The V3 source should retain 19
headers: 18 subagents and one empty top-level skeleton. The recent July source
remains unchanged with its 13 headers.

### Successful July 13 Run

The corrected version 0.7 recovery completed with these operations:

| Role | Operation ID | Recorded chats | Result |
|---|---|---:|---|
| Canary | `20260713T181850Z-351dbe38` | 1 | Header, composer data, target UI state, and visible history verified. |
| Main batch | `20260713T182256Z-bcec8dd3` | 66 | All indexed and embedded identities verified; full history became visible. |

The final UI check was decisive: the canary appeared as one history entry before
the batch, and the remaining history appeared after the batch. This proves the
result was not merely an already-open chat retained by workspace UI state.

## Step 11: Undo An Operation If Its Result Is Wrong

Quit Cursor, then preview:

```bash
restore-cursor undo <operation-id>
```

Apply the undo only after its counts are correct:

```bash
restore-cursor undo <operation-id> --apply
```

Use the canary or batch operation ID deliberately. Undo uses that manifest's exact
composer IDs and does not touch the target's original two headers or the other
restore operation. It refuses a partial undo if any recorded header has moved
somewhere unexpected.

## Step 12: Retain Or Remove The Emergency Snapshots

List operations and snapshots:

```bash
restore-cursor backups
```

Keep both snapshots until both operations verify successfully and the chats look
correct in Cursor. For future recoveries, remove obsolete failed, undone, and
superseded snapshots first; retain one successful snapshot for a default 14-day
fallback period. APFS clone sizes are apparent and shared, so use filesystem free
space before and after cleanup to measure actual reclaimed storage.

Then preview removing each operation's large files:

```bash
restore-cursor delete-backup <canary-operation-id>
restore-cursor delete-backup <batch-operation-id>
```

Then apply each deletion:

```bash
restore-cursor delete-backup <canary-operation-id> --apply
restore-cursor delete-backup <batch-operation-id> --apply
```

This retains both small manifests and both logical undo records. It removes the
ability to perform an emergency full-database rollback from those snapshots.

For this successful run, substitute `20260713T181850Z-351dbe38` and
`20260713T182256Z-bcec8dd3` for the canary and batch placeholders. Older failed or
undone operation snapshots can be reviewed and removed separately; never delete a
snapshot merely because its operation ID looks old.

### Cleanup Result For This Run

After the recovered history was tested in Cursor, all five obsolete snapshots were
deleted first. The successful batch snapshot was deleted next, while the successful
canary snapshot remained as the final emergency fallback. The canary snapshot was
deleted last after explicit confirmation of the consequence.

Final read-only audit:

```text
All seven operations: snapshot deleted, disk_bytes 0
Backup directory: 88K
Full-database emergency rollback: no longer available
Small manifests and scoped logical undo metadata: retained
```

## Step 13: Leave Subagents And Other Sources Alone

Do not restore the recent 13-header source merely because its `any_activity` is
fresh: its useful records are subagents, while its three top-level headers are
empty. Likewise, do not restore the V3 source's remaining 19 headers, the other
two-header sources, or `HISTORY-SAFE` merely because they appear in the table.

The main transcripts are now recovered. Subagents can be inspected and handled
later as a separate enhancement without putting the main-chat recovery at risk.

## Effect On Other Cursor Workspaces

The normal restore is narrowly scoped even though Cursor stores all workspaces in
one global database.

| Area | Effect of the canary plus main-chat batch |
|---|---|
| Selected V3 source | Exactly 67 non-empty top-level headers move; 18 subagents and one empty skeleton remain. |
| Current target | Receives those 67 main headers; its existing 2 remain unchanged. |
| Target workspace state | The canary ID is added to selected/focused arrays; existing IDs remain. |
| Other `composerHeaders` rows | Not selected or updated. |
| Selected `composerData` rows | Only the top-level `workspaceIdentifier` changes; original identities are recorded for undo. |
| Bubble/checkpoint/request records | Not rewritten or moved; they remain keyed by composer ID. |
| `ItemTable` and workspace metadata | Not modified. |
| Other projects' `workspaceStorage` folders | Not modified. |
| Emergency snapshot | Contains the entire global database, including other workspaces. |

The SQL update is restricted by both the recorded source and the exact composer IDs
captured before the backup. Immediately before writing, the tool confirms every
recorded ID still belongs to the expected source. Existing target rows are not
replaced.

### Remaining Risks

1. Selecting the wrong source intentionally moves that source's headers. Preview,
   sample titles, body inspection, and one-source-at-a-time recovery reduce this
   risk. Logical undo reverses that exact operation.
2. Cursor schema changes could make local assumptions stale. `doctor` checks the
   required tables, but this remains an unofficial recovery tool built from local
   schema inspection.
3. Cursor writing concurrently could race with recovery. The process guard and the
   requirement to quit Cursor address this.
4. Any write to a global database carries corruption risk. The pre-write snapshot
   exists for this reason, and the update itself is transactional.
5. Manually replacing the live database with the full snapshot would roll back
   changes made by every Cursor workspace after that snapshot. Prefer logical
   `undo`, which touches only the recorded operation.

So a successful V3 restore does not alter the chat ownership of unrelated
workspaces. The principal cross-workspace risk comes only from the shared database
file itself or from manually rolling back that entire file, not from the scoped
header update used by the normal restore command.
