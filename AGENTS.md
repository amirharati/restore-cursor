# Agent Instructions

This is the canonical cold-start instruction file for AI agents working in this
repository. Tool-specific instruction files must point here rather than duplicate
these rules.

## First Actions

1. Read `README.md`.
2. Read `docs/ai-assisted-recovery.md`.
3. Read `docs/restore-playbook.md` and `docs/how-it-works.md`.
4. If the task concerns the `workbench_agent` incident, also read
   `docs/workbench-agent-workflow.md`.
5. Run `git status --short` and preserve unrelated user changes.
6. Determine whether the user is recovering live data or developing the tool.

Do not rely on prior chat context. Reconstruct state from these files, read-only
commands, operation manifests, and the user's current UI observations.

## Non-Negotiable Recovery Rules

Use shared-control mode when assisting with live Cursor recovery.

- The agent may run and interpret read-only `restore-cursor` commands.
- The agent must never execute a command containing `--apply`.
- The user personally types every `--apply` command.
- Work one checkpoint at a time; do not give a chain of write commands.
- Explain exactly what the next write changes and where its snapshot is stored.
- Never write to Cursor SQLite databases directly.
- Never replace a live database with a snapshot as a routine recovery step.
- Never kill Cursor or bypass the process guard.
- Never guess a source from a similar folder name. Confirm path/repository, titles,
  activity, body counts, and a bounded transcript.
- Require one non-empty top-level canary before any batch.
- After a write, verify while Cursor is closed. Then require the user to check the
  real history list and open the transcript.
- An already-open chat without a history entry is a failed canary.
- Do not continue after any failed or ambiguous checkpoint. Preview logical undo.
- Do not delete snapshots until database verification and the real UI check pass.
- Prefer exact logical undo over a full snapshot rollback. A full rollback can
  revert unrelated Cursor workspaces.

Read-only commands include `--version`, `doctor`, `project`, `search`, `inspect`,
`chat`, `verify`, `backups`, and preview forms of `recover`, `recover-chat`, `undo`,
`delete-backup`, and `cleanup` without `--apply`.

## Required Recovery Sequence

1. Confirm the installed version and database health.
2. Resolve the target from the current project folder.
3. Inspect candidate sources with bodies and activity sorting.
4. Preview a recognizable top-level composer transcript.
5. Preview one-chat recovery.
6. Ask the user to quit Cursor and manually apply the canary.
7. Record the operation ID and verify while Cursor remains closed.
8. Ask the user to confirm a real history entry and readable transcript.
9. Preview the filtered `--top-level-only --nonempty` batch.
10. Ask the user to quit Cursor and manually apply the batch.
11. Verify while Cursor remains closed, then check several chats in the UI.
12. Retain snapshots for a deliberate period and clean them up one by one.

Maintain this ledger in the conversation:

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

## Current Handoff: July 13, 2026

This is historical context, not permission to reuse IDs blindly. Confirm current
state with read-only commands before advising a write.

```text
Project: /Users/amir/projects/personal_tools/workbench_agent
Target: 1a8ae8259fbc00c108c9fc21af2b3782
V3 source: 44f993e64115ff898bc82a01e5c7f798
Canary composer: 334112f6-df29-412a-8a9d-6b769d3f99b2
Canary title: Code and documentation review
Successful canary operation: 20260713T181850Z-351dbe38
Successful batch operation: 20260713T182256Z-bcec8dd3
Batch selection: 66 non-empty top-level chats
UI result: user confirmed the full history is visible and working
Intentionally excluded: 18 subagents and one empty top-level skeleton
Pending checkpoint: list and classify backups before deleting any snapshot
```

The successful recovery used version `0.7.0`. It updated and verified all three
workspace associations:

1. `composerHeaders.workspaceId`
2. `composerHeaders.value.workspaceIdentifier`
3. `composerData.workspaceIdentifier`

The canary also updated the target workspace's selected/focused composer arrays.
Earlier operations from versions 0.5.1 and 0.6.1 were incomplete and were undone.
Consult the worked example before classifying their snapshots.

## Development Rules

When changing the tool itself:

- Read the existing implementation before editing.
- Keep changes scoped and preserve the dependency-free Python design.
- Use structured JSON and SQLite operations, not raw text replacement.
- Keep old manifest versions readable when evolving the manifest schema.
- Add focused lifecycle tests for apply, verify, undo, WAL mode, and workspace UI
  state when those behaviors change.
- Run:

```bash
env PYTHONPATH=src python3 -m unittest discover -s tests -v
```

- If runtime code or version changes, rerun `./install.sh` and confirm
  `restore-cursor --version`.
- Installation copies source to `~/.local/share/restore-cursor`; editing the repo
  does not update the installed command automatically.
- Do not commit databases, snapshots, generated runtimes, caches, or secrets.
- Update the workflow and safety documentation whenever behavior changes.

## Communication Style

Be calm and explicit. Translate counts and IDs into plain language. Show the exact
next command, but reserve changing commands for the user. State what success looks
like before the command is run. When evidence conflicts, stop and investigate
instead of improvising.
