# AI-Assisted Recovery Guide

This guide describes a shared-control recovery: an AI agent reads outputs,
explains risks, and keeps the workflow organized, while the user personally runs
every command that changes Cursor data. This is safer than either guessing alone
or giving an autonomous agent unrestricted control of Cursor's database.

Use the agent from Codex, VS Code, a terminal assistant, or another non-Cursor
environment. Cursor must be fully quit during writes, so an agent running inside
Cursor is a poor choice for the apply stages.

## Cold-Start Rule Discovery

The canonical repository rule is [`AGENTS.md`](../AGENTS.md). No instruction
filename is recognized by every agent product, so the repository also provides
small discovery files for Claude, Gemini, GitHub Copilot, and Cursor. Those files
only point to `AGENTS.md`; they do not duplicate policy. The README also directs
unknown agents there. If an agent does not automatically load repository rules,
begin the session with: “Read and follow `AGENTS.md` before doing any work.”

## Division Of Responsibility

| Participant | Responsibility |
|---|---|
| User | Confirms the project and source, quits Cursor, manually runs every `--apply`, checks the real Cursor UI, and decides when snapshots may be deleted. |
| AI agent | Reads the documentation, proposes one checkpoint at a time, explains output, checks counts and IDs, records operation IDs, and stops when evidence is ambiguous. |
| `restore-cursor` | Performs immutable previews, enforces source guards, creates snapshots, writes exact manifests, applies scoped transactions, verifies, and supports logical undo. |

The AI agent is an advisor and verifier, not the database operator.

## Commands The Agent May Run

These commands are read-only when used without `--apply`:

```text
restore-cursor --version
restore-cursor doctor
restore-cursor project <folder>
restore-cursor search <terms>
restore-cursor inspect <workspace> [--with-bodies]
restore-cursor chat <composer> [--tail]
restore-cursor recover-chat <folder> <composer>
restore-cursor recover <folder> --source <source> [filters]
restore-cursor verify <operation-id>
restore-cursor backups
restore-cursor undo <operation-id>
restore-cursor delete-backup <operation-id>
restore-cursor cleanup [options]
```

An agent with terminal access may run those commands and summarize the results.
It should still show the exact command and important output to the user.

## Commands Reserved For The User

The user should manually type every command containing `--apply`, including:

```text
restore-cursor recover-chat ... --apply
restore-cursor recover ... --apply
restore-cursor undo ... --apply
restore-cursor delete-backup ... --apply
restore-cursor cleanup ... --apply
```

The agent must not:

- write to Cursor SQLite databases directly;
- add `--apply` on the user's behalf;
- kill Cursor processes to bypass the process guard;
- guess a source workspace from folder-name similarity;
- replace the live database with a snapshot;
- delete snapshots before database verification and a real UI check;
- continue from a failed canary to a batch restore.

## Start The Agent Session

Open this repository in the non-Cursor environment and give the agent this prompt:

```text
Help me recover Cursor chat history using the restore-cursor repository.

Read README.md, docs/restore-playbook.md, docs/how-it-works.md, and the relevant
worked example before advising me.

Use shared-control mode:
1. Work one checkpoint at a time.
2. You may run read-only restore-cursor commands and explain their output.
3. Never run a command containing --apply. I will type every changing command.
4. Never write to Cursor's SQLite databases directly.
5. Do not select a source until I confirm its path, titles, activity, and bodies.
6. Require a one-chat canary before a batch.
7. Require database verification while Cursor is closed, then a real history-list
   check in Cursor.
8. Keep a ledger of source ID, target ID, composer ID, operation IDs, counts, and
   snapshot status.
9. If evidence is inconsistent, stop and explain it instead of improvising.

Current project folder: <absolute-project-path>
```

Do not paste entire private transcripts into the prompt. Titles, IDs, counts, and a
small bounded `chat --messages` sample are normally enough.

## Checkpoint Workflow

### 1. Establish The Tool And Project

The agent checks:

```bash
restore-cursor --version
restore-cursor doctor
restore-cursor project <folder>
```

The agent explains which exact-path workspace is the target and why each other
workspace is only a candidate source. The user confirms the folder and intended
repository before continuing.

### 2. Prove The Source Contains Real Chats

The agent uses activity sorting and body inspection:

```bash
restore-cursor inspect <source-id> \
  --with-bodies \
  --sort activity \
  --order newest

restore-cursor chat <composer-id> --messages 12 --tail
```

The checkpoint passes only when the intended top-level chat has a recognizable
title or transcript, stored bubbles, readable text, and a plausible source.
Header dates alone are not sufficient because Cursor can leave them stale.

### 3. Preview One Canary

```bash
restore-cursor recover-chat <folder> <composer-id>
```

The agent checks the full resolved composer ID, title, source, target, archive and
subagent flags, body counts, target UI state, and this line:

```text
Embedded workspace identity: header and composer data will follow target
```

The agent then asks the user to fully quit Cursor. It provides the exact apply
command, but the user types it.

### 4. User Applies The Canary

```bash
restore-cursor recover-chat <folder> <composer-id> --apply
```

The user gives the output back to the agent. The agent records the operation ID and
checks that exactly one indexed header, embedded header identity, embedded composer
data identity, and target UI attachment were updated.

### 5. Verify Before Opening Cursor

With Cursor still closed:

```bash
restore-cursor verify <canary-operation-id>
```

The agent requires all recorded counts and identity checks to pass. The user then
opens Cursor and confirms both conditions:

1. The canary is listed in workspace history.
2. Opening it displays the expected transcript.

An already-open chat without a history entry is a failed canary. Stop there.

### 6. Preview The Main-Chat Batch

After the canary succeeds:

```bash
restore-cursor recover <folder> \
  --source <source-id> \
  --top-level-only \
  --nonempty
```

The agent compares source, selected, and existing-target counts against the earlier
inventory. It should explain what remains excluded, especially subagents, archived
records, and empty skeletons.

### 7. User Applies And Verifies The Batch

The user quits Cursor and manually runs:

```bash
restore-cursor recover <folder> \
  --source <source-id> \
  --top-level-only \
  --nonempty \
  --apply
```

The agent records the new operation ID. With Cursor still closed, it asks for:

```bash
restore-cursor verify <batch-operation-id>
```

The user then opens Cursor, inspects the history list, and opens several old and
recent chats. Database verification cannot replace this UI check.

### 8. Stop Or Undo On Any Mismatch

Do not perform more restores after a failed canary or batch. First preview the exact
logical undo:

```bash
restore-cursor undo <operation-id>
```

After the agent confirms that every recorded chat is at the expected target, the
user quits Cursor and manually applies:

```bash
restore-cursor undo <operation-id> --apply
```

Then verify the same operation ID. Prefer logical undo over replacing the complete
database, because a full snapshot rollback affects every Cursor workspace changed
since the snapshot.

### 9. Clean Up Deliberately

Keep successful snapshots until the database checks pass, the history looks right,
several transcripts open, and Cursor has been used normally for a while. List all
operations:

```bash
restore-cursor backups
```

For each candidate, the agent explains its status and whether it is a failed,
undone, canary, or successful batch operation. The user previews deletion first:

```bash
restore-cursor delete-backup <operation-id>
```

Only the user runs the form with `--apply`. Snapshot deletion retains the small
manifest and logical undo metadata but removes emergency full-database rollback.

## Recovery Ledger

Ask the agent to maintain this compact record in the conversation:

```text
Project path:
Target workspace ID:
Confirmed source workspace ID:
Canary composer ID and title:
Canary operation ID and verification:
Canary visible in history: yes/no
Batch filters and selected count:
Batch operation ID and verification:
Sample transcripts opened:
Snapshots retained/deleted:
Excluded records:
```

This ledger makes a long recovery auditable and prevents accidental reuse of an
operation ID from a failed or superseded attempt.

## Special Guidance For AI Agents

When helping a user, communicate in small checkpoints. Explain what a count means,
what will change, where the snapshot will be stored, and what evidence is needed
before the next step. Never treat `Verification: OK` as proof of UI success, and
never treat an open tab as proof that the chat belongs to history.

The safest useful agent is calm, skeptical of ambiguous evidence, and comfortable
saying “stop here” before a write.
