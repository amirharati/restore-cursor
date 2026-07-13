from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import __version__
from .cursor_db import (
    connect_readonly,
    composer_messages,
    composer_preview,
    count_headers,
    current_project_workspace,
    cursor_db_path_from_env,
    ensure_schema,
    header_workspace_map,
    list_workspaces,
    normalize_workspace_path,
    project_git_remote,
    project_workspaces,
    prepare_workspace_composer_attachment,
    prepare_workspace_identity_rewrite,
    resolve_composer_id,
    restore_headers,
    restore_headers_with_workspace_state,
    search_headers,
    undo_headers,
    undo_headers_with_workspace_state,
    workspace_header_summary,
    workspace_headers,
    workspace_composer_attachment_status,
    workspace_identity_status,
    workspace_selected_header_ids,
    workspace_state_db_path,
)
from .operations import (
    create_restore_operation,
    default_backup_root,
    delete_snapshot,
    list_manifests,
    load_manifest,
    save_manifest,
    snapshot_disk_bytes,
    snapshot_state,
    utc_now,
)


def print_rows(rows, columns: list[str]) -> None:
    rows = list(rows)
    if not rows:
        print("No rows.")
        return
    def cell(row, col: str) -> str:
        value = row[col]
        return "" if value is None else str(value)

    widths = {
        col: max(len(col), *(len(cell(row, col)) for row in rows))
        for col in columns
    }
    print("  ".join(col.ljust(widths[col]) for col in columns))
    print("  ".join("-" * widths[col] for col in columns))
    for row in rows:
        print("  ".join(cell(row, col).ljust(widths[col]) for col in columns))


def nonnegative_int(value: str) -> int:
    parsed = int(value)
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be zero or greater")
    return parsed


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed


def require_cursor_stopped(*, confirmed: bool) -> None:
    try:
        result = subprocess.run(
            ["pgrep", "-fl", "Cursor.app|Cursor Helper"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        if confirmed:
            print("Could not check processes; using your --confirm-cursor-quit assertion.")
            return
        raise SystemExit(
            "Could not check whether Cursor is running. Quit Cursor and add "
            "--confirm-cursor-quit."
        )
    output = result.stdout.strip()
    if output:
        print("Cursor appears to be running:")
        print(output)
        raise SystemExit("Quit Cursor completely before changing its database.")
    if result.returncode not in {0, 1}:
        if confirmed:
            print("Could not check processes; using your --confirm-cursor-quit assertion.")
            return
        raise SystemExit(
            "Could not verify that Cursor is stopped. Quit Cursor and add "
            "--confirm-cursor-quit."
        )


def open_readonly(db_path: Path):
    if not db_path.exists():
        raise SystemExit(f"Cursor DB not found: {db_path}")
    conn = connect_readonly(db_path)
    ensure_schema(conn)
    return conn


def cmd_doctor(args: argparse.Namespace) -> int:
    db_path = args.db
    print(f"DB: {db_path}")
    if not db_path.exists():
        print("Status: missing")
        return 1
    conn = open_readonly(db_path)
    try:
        header_count = conn.execute("select count(*) as n from composerHeaders").fetchone()["n"]
        body_count = conn.execute("select count(*) as n from cursorDiskKV").fetchone()["n"]
        print("Status: readable")
        print(f"Composer headers: {header_count}")
        print(f"cursorDiskKV rows: {body_count}")
        print(f"DB size: {db_path.stat().st_size:,} bytes")
    finally:
        conn.close()
    return 0


def cmd_workspaces(args: argparse.Namespace) -> int:
    conn = open_readonly(args.db)
    try:
        rows = [
            {"workspace_id": item.workspace_id, "path": item.path, "source": item.source}
            for item in list_workspaces(conn)
        ]
        if args.filter:
            needle = args.filter.lower()
            rows = [
                row
                for row in rows
                if needle in row["workspace_id"].lower() or needle in row["path"].lower()
            ]
        print_rows(rows, ["workspace_id", "path", "source"])
    finally:
        conn.close()
    return 0


def _project_sources(
    db_path: Path, project_path: Path
) -> tuple[str, list[dict[str, object]]]:
    project_path = project_path.expanduser().resolve(strict=False)
    if not project_path.is_dir():
        raise SystemExit(f"Project folder not found: {project_path}")
    conn = open_readonly(db_path)
    try:
        matches = project_workspaces(conn, project_path)
        target_match = current_project_workspace(matches)
        if target_match is None:
            raise SystemExit(
                "No active Cursor workspace was found for this exact folder. "
                "Open the folder in Cursor once, then run this command again."
            )
        target_id = target_match.workspace.workspace_id
        rows: list[dict[str, object]] = []
        option = 0
        for match in matches:
            workspace = match.workspace
            (
                headers,
                top_level,
                subagents,
                latest_header,
                main_activity,
                any_activity,
            ) = workspace_header_summary(conn, workspace.workspace_id)
            if workspace.workspace_id == target_id:
                role = "TARGET"
                option_value: int | str = "-"
            elif headers > 0:
                option += 1
                role = "source"
                option_value = option
            else:
                role = "empty"
                option_value = "-"
            modified = (
                datetime.fromtimestamp(workspace.storage_modified).strftime(
                    "%Y-%m-%d %H:%M:%S"
                )
                if workspace.storage_modified is not None
                else ""
            )
            rows.append(
                {
                    "option": option_value,
                    "role": role,
                    "headers": headers,
                    "top_level": top_level,
                    "subagents": subagents,
                    "latest_header": latest_header or "",
                    "main_activity": main_activity or "",
                    "any_activity": any_activity or "",
                    "relation": match.relation,
                    "workspace_id": workspace.workspace_id,
                    "workspace_modified": modified,
                    "path": workspace.path,
                }
            )
    finally:
        conn.close()
    return target_id, rows


def _print_project_resolution(project_path: Path, target_id: str, rows) -> None:
    print(f"Project: {project_path.expanduser().resolve(strict=False)}")
    repo = project_git_remote(project_path.expanduser().resolve(strict=False))
    print(f"Git repository: {repo or 'not detected'}")
    print(f"Current target workspace: {target_id}")
    print("Target rule: newest Cursor workspaceStorage entry for this exact path")
    print()
    print_rows(
        rows,
        [
            "option",
            "role",
            "headers",
            "top_level",
            "subagents",
            "latest_header",
            "main_activity",
            "any_activity",
            "relation",
            "workspace_id",
            "workspace_modified",
            "path",
        ],
    )


def cmd_project(args: argparse.Namespace) -> int:
    target_id, rows = _project_sources(args.db, args.project_path)
    _print_project_resolution(args.project_path, target_id, rows)
    sources = [row for row in rows if row["role"] == "source"]
    if sources:
        print("\nUse `restore-cursor recover <folder>` to select a source by number.")
    else:
        print("\nNo related workspace with chat headers was found.")
    return 0


def _select_project_source(rows, selector: str | None) -> dict[str, object] | None:
    sources = [row for row in rows if row["role"] == "source"]
    if not sources:
        return None
    if selector is None:
        if len(sources) == 1:
            return sources[0]
        if not sys.stdin.isatty():
            return None
        answer = input("\nEnter source option number (or q to quit): ").strip()
        if answer.lower() == "q":
            return None
        selector = answer

    if selector.isdigit():
        matches = [row for row in sources if str(row["option"]) == selector]
    else:
        normalized_selector = normalize_workspace_path(selector)
        matches = [
            row
            for row in sources
            if str(row["workspace_id"]).startswith(selector)
            or normalize_workspace_path(str(row["path"])) == normalized_selector
        ]
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"No source matches: {selector}")
    raise SystemExit(
        "That source is ambiguous. Choose its option number from this invocation."
    )


def cmd_recover(args: argparse.Namespace) -> int:
    target_id, rows = _project_sources(args.db, args.project_path)
    _print_project_resolution(args.project_path, target_id, rows)
    selected = _select_project_source(rows, args.source)
    if selected is None:
        print("\nNo source selected; no changes made.")
        print("Rerun with `--source <option-number>` or run interactively.")
        return 0

    print(
        f"\nSelected source {selected['option']}: "
        f"{selected['workspace_id']} ({selected['headers']} headers)"
    )
    restore_args = argparse.Namespace(
        **vars(args),
        target=target_id,
        allow_unknown_target=False,
    )
    restore_args.source = str(selected["workspace_id"])
    return cmd_restore(restore_args)


def cmd_search(args: argparse.Namespace) -> int:
    conn = open_readonly(args.db)
    try:
        rows = search_headers(conn, args.terms, args.limit)
        print_rows(
            rows,
            [
                "composerId",
                "workspaceId",
                "name",
                "created",
                "updated",
                "mode",
                "isArchived",
                "isSubagent",
            ],
        )
    finally:
        conn.close()
    return 0


def cmd_inspect(args: argparse.Namespace) -> int:
    conn = open_readonly(args.db)
    try:
        sort_by = args.sort or ("activity" if args.with_bodies else "header")
        rows = workspace_headers(
            conn,
            args.workspace_id,
            with_bodies=args.with_bodies,
            sort_by=sort_by,
            newest_first=args.order == "newest",
        )
        columns = [
            "composerId",
            "name",
            "created",
            "updated",
            "activity",
            "mode",
            "isArchived",
            "isSubagent",
        ]
        if args.with_bodies:
            columns += [
                "composer_bytes",
                "bubbles",
                "text_messages",
                "bubble_bytes",
            ]
        else:
            columns += ["header_bytes"]
        print_rows(rows, columns)
        if not args.with_bodies:
            print(
                "\nHeader metadata and indexed composer activity only. Add "
                "--with-bodies to show bubble counts, readable text counts, and "
                "stored bubble bytes."
            )
    finally:
        conn.close()
    return 0


def _display_name(row) -> str:
    return str(row["name"] or "(untitled)")


def cmd_chat(args: argparse.Namespace) -> int:
    conn = open_readonly(args.db)
    try:
        composer_id = resolve_composer_id(conn, args.composer)
        preview = composer_preview(conn, composer_id)
        messages = composer_messages(
            conn,
            composer_id,
            limit=args.messages,
            tail=args.tail,
        )
    finally:
        conn.close()

    header = preview["header"]
    is_subagent = bool(header["isSubagent"])
    kind = "subagent"
    if is_subagent and preview["subagent_type"]:
        kind += f" ({preview['subagent_type']})"
    elif not is_subagent:
        kind = "top-level"

    print(f"Composer: {composer_id}")
    print(f"Title: {_display_name(header)}")
    print(f"Kind: {kind}")
    print(f"Workspace: {header['workspaceId']}")
    print(f"Mode: {header['mode'] or ''}")
    print(f"Created: {header['created'] or ''}")
    print(f"Updated: {header['updated'] or ''}")
    print(f"Archived: {bool(header['isArchived'])}")
    print(f"Composer data: {preview['composer_bytes']:,} bytes")
    print(f"Bubbles: {preview['bubbles']:,}")
    print(f"Readable text messages: {preview['text_messages']:,}")

    parent = preview["parent"]
    parent_id = preview["parent_id"]
    if parent is not None:
        print("\nParent:")
        print(f"  {parent['composerId']}  {_display_name(parent)}")
        print(f"  workspace: {parent['workspaceId']}")
    elif parent_id:
        print(f"\nParent header missing: {parent_id}")

    children = preview["children"]
    if children:
        print(f"\nDirect children ({len(children)}):")
        child_rows = [
            {
                "composerId": child["composerId"],
                "kind": "subagent" if child["isSubagent"] else "top-level",
                "workspaceId": child["workspaceId"],
                "name": _display_name(child),
            }
            for child in children
        ]
        print_rows(child_rows, ["composerId", "kind", "workspaceId", "name"])
    missing_children = preview["missing_child_ids"]
    if missing_children:
        print("\nChild headers missing:")
        for child_id in missing_children:
            print(f"  {child_id}")

    section = "Last" if args.tail else "First"
    print(f"\n{section} {len(messages)} readable messages:")
    if not messages:
        print("No readable text messages found.")
        return 0
    for index, message in enumerate(messages, start=1):
        role = {1: "USER", 2: "ASSISTANT"}.get(message["type"], f"TYPE-{message['type']}")
        text = str(message["text"] or "")
        if len(text) > args.max_chars:
            omitted = len(text) - args.max_chars
            text = f"{text[:args.max_chars]}\n...[{omitted:,} characters omitted]"
        print(f"\n[{index}] {role}  {message['created'] or ''}")
        print(text)
    return 0


def _apply_selected_restore(
    args: argparse.Namespace,
    *,
    source: str,
    target: str,
    composer_ids: list[str],
    selection: dict[str, object],
    workspace_state: dict[str, object] | None = None,
    workspace_identity: dict[str, object] | None = None,
) -> int:
    require_cursor_stopped(confirmed=args.confirm_cursor_quit)
    backup_root = args.backup_dir or default_backup_root(args.db)
    operation_dir, manifest = create_restore_operation(
        args.db,
        backup_root,
        source,
        target,
        composer_ids,
        workspace_state=workspace_state,
        workspace_identity=workspace_identity,
    )
    manifest["selection"] = selection
    save_manifest(operation_dir, manifest)
    print(f"Operation: {manifest['operation_id']}")
    print(f"Emergency snapshot: {operation_dir}")
    try:
        if workspace_state is None:
            updated = restore_headers(
                args.db,
                source,
                target,
                composer_ids=composer_ids,
                workspace_identity=workspace_identity,
            )
        else:
            updated = restore_headers_with_workspace_state(
                args.db,
                source,
                target,
                composer_ids,
                workspace_state,
                workspace_identity,
            )
    except Exception as exc:
        manifest["status"] = "apply_failed"
        manifest["error"] = str(exc)
        save_manifest(operation_dir, manifest)
        raise
    manifest["status"] = "applied"
    manifest["applied_at"] = utc_now()
    manifest["updated_headers"] = updated
    save_manifest(operation_dir, manifest)
    print(f"Updated headers: {updated}")
    if workspace_identity is not None:
        print(f"Updated embedded header identities: {updated}")
        print(
            "Updated embedded composer data identities: "
            + str(workspace_identity.get("composer_data_count", 0))
        )
    if workspace_state is not None:
        print("Attached composers to target workspace UI state: " + str(len(composer_ids)))
    print("Keep the operation ID. Run `restore-cursor verify <id>` before cleanup.")
    return 0


def cmd_recover_chat(args: argparse.Namespace) -> int:
    target_id, rows = _project_sources(args.db, args.project_path)
    related_workspace_ids = {str(row["workspace_id"]) for row in rows}

    conn = open_readonly(args.db)
    try:
        composer_id = resolve_composer_id(conn, args.composer)
        preview = composer_preview(conn, composer_id)
    finally:
        conn.close()

    header = preview["header"]
    source_id = str(header["workspaceId"])
    target_state_path = workspace_state_db_path(target_id)
    workspace_state = prepare_workspace_composer_attachment(
        target_state_path, [composer_id]
    )
    print(f"Project: {args.project_path.expanduser().resolve(strict=False)}")
    print(f"Composer: {composer_id}")
    print(f"Title: {_display_name(header)}")
    print(f"Source: {source_id}")
    print(f"Target: {target_id}")
    print(f"Archived: {bool(header['isArchived'])}")
    print(f"Bubbles: {preview['bubbles']:,}")
    print(f"Readable text messages: {preview['text_messages']:,}")
    print(f"Direct child headers found: {len(preview['children']):,}")
    print("Selection: exactly 1 top-level composer header; child headers stay in place")
    print(f"Target workspace UI state: {target_state_path}")
    print("UI attachment: selectedComposerIds and lastFocusedComposerIds")

    if source_id == target_id:
        raise SystemExit("This composer is already associated with the current target.")
    if source_id not in related_workspace_ids:
        raise SystemExit(
            "The composer does not belong to an exact-path or same-repository "
            "workspace for this project; refusing recovery."
        )
    if bool(header["isSubagent"]):
        raise SystemExit(
            "The selected composer is a subagent. `recover-chat` accepts only "
            "top-level main chats."
        )
    if bool(header["isArchived"]):
        raise SystemExit(
            "The selected main chat is archived and may not become visible; "
            "refusing the canary recovery."
        )
    if int(preview["text_messages"]) == 0:
        raise SystemExit(
            "The selected main chat has no readable text messages; refusing to "
            "move an empty session skeleton."
        )

    workspace_identity = prepare_workspace_identity_rewrite(
        args.db,
        source_id,
        target_id,
        args.project_path,
        [composer_id],
    )
    print("Embedded workspace identity: header and composer data will follow target")

    if not args.apply:
        print(
            "\nDry run only. Add --apply after quitting Cursor to move this one header."
        )
        return 0

    return _apply_selected_restore(
        args,
        source=source_id,
        target=target_id,
        composer_ids=[composer_id],
        selection={
            "kind": "single_main_chat",
            "composer_id": composer_id,
            "title": _display_name(header),
            "include_subagents": False,
        },
        workspace_state=workspace_state,
        workspace_identity=workspace_identity,
    )


def cmd_restore(args: argparse.Namespace) -> int:
    if args.source == args.target:
        raise SystemExit("Source and target workspace IDs are identical.")

    read_conn = open_readonly(args.db)
    try:
        source_count = count_headers(read_conn, args.source)
        target_count = count_headers(read_conn, args.target)
        known_workspaces = {
            item.workspace_id: item for item in list_workspaces(read_conn)
        }
        target_known = args.target in known_workspaces
        composer_ids = workspace_selected_header_ids(
            read_conn,
            args.source,
            top_level_only=args.top_level_only,
            nonempty=args.nonempty,
        )
        selected_ids = set(composer_ids)
        samples = [
            row
            for row in workspace_headers(read_conn, args.source, with_bodies=False)
            if str(row["composerId"]) in selected_ids
        ][:10]
    finally:
        read_conn.close()

    print(f"Source: {args.source}")
    print(f"Target: {args.target}")
    print(f"Source headers: {source_count}")
    print(f"Selected headers: {len(composer_ids)}")
    print(
        "Selection: "
        + ("top-level only" if args.top_level_only else "top-level and subagent")
        + (", stored bubbles required" if args.nonempty else ", empty headers allowed")
    )
    print(f"Existing target headers: {target_count}")
    print(f"Target is a known Cursor workspace: {target_known}")
    print()
    print_rows(
        samples,
        ["composerId", "name", "created", "updated", "mode", "isArchived", "isSubagent"],
    )

    if not args.apply:
        print(
            "\nDry run only. Add --apply after quitting Cursor to update headers "
            "and embedded workspace identities."
        )
        return 0

    if not composer_ids:
        raise SystemExit("No source headers match the selection; refusing to write.")
    if not target_known and not args.allow_unknown_target:
        raise SystemExit(
            "Target is not present in Cursor workspace metadata; refusing to write. "
            "Check the ID or add --allow-unknown-target after verifying it manually."
        )
    if not target_known:
        raise SystemExit(
            "The target workspace path is unavailable, so its embedded identity "
            "cannot be built safely; refusing to write."
        )

    workspace_identity = prepare_workspace_identity_rewrite(
        args.db,
        args.source,
        args.target,
        known_workspaces[args.target].path,
        composer_ids,
    )

    return _apply_selected_restore(
        args,
        source=args.source,
        target=args.target,
        composer_ids=composer_ids,
        selection={
            "kind": "workspace_filter",
            "top_level_only": args.top_level_only,
            "nonempty": args.nonempty,
        },
        workspace_identity=workspace_identity,
    )


def _backup_root(args: argparse.Namespace) -> Path:
    return args.backup_dir or default_backup_root(args.db)


def cmd_backups(args: argparse.Namespace) -> int:
    rows = []
    for operation_dir, manifest in list_manifests(_backup_root(args)):
        snapshot = manifest.get("snapshot", {})
        methods = sorted(set(snapshot.get("methods", {}).values()))
        rows.append(
            {
                "operation_id": manifest.get("operation_id"),
                "created": manifest.get("created_at"),
                "status": manifest.get("status"),
                "source": manifest.get("source_workspace_id"),
                "target": manifest.get("target_workspace_id"),
                "headers": manifest.get("header_count"),
                "snapshot": snapshot_state(operation_dir, manifest),
                "disk_bytes": f"{snapshot_disk_bytes(operation_dir, manifest):,}",
                "method": ",".join(methods),
            }
        )
    print_rows(
        rows,
        [
            "operation_id",
            "created",
            "status",
            "headers",
            "snapshot",
            "disk_bytes",
            "method",
            "source",
            "target",
        ],
    )
    return 0


def _location_counts(
    db_path: Path, manifest: dict
) -> tuple[list[str], str, str, int, int, int]:
    composer_ids = list(manifest.get("composer_ids", []))
    source = str(manifest["source_workspace_id"])
    target = str(manifest["target_workspace_id"])
    conn = open_readonly(db_path)
    try:
        locations = header_workspace_map(conn, composer_ids)
    finally:
        conn.close()
    at_target = sum(locations.get(composer_id) == target for composer_id in composer_ids)
    at_source = sum(locations.get(composer_id) == source for composer_id in composer_ids)
    elsewhere = len(composer_ids) - at_target - at_source
    return composer_ids, source, target, at_target, at_source, elsewhere


def cmd_verify(args: argparse.Namespace) -> int:
    _, manifest = load_manifest(_backup_root(args), args.operation_id)
    composer_ids, _, _, at_target, at_source, elsewhere = _location_counts(
        args.db, manifest
    )
    expected = "source" if manifest.get("status") == "undone" else "target"
    expected_count = at_source if expected == "source" else at_target
    print(f"Operation: {args.operation_id}")
    print(f"Manifest status: {manifest.get('status')}")
    print(f"Recorded headers: {len(composer_ids)}")
    print(f"At target: {at_target}")
    print(f"At source: {at_source}")
    print(f"Missing or elsewhere: {elsewhere}")
    ui_ok = True
    workspace_state = manifest.get("workspace_state")
    if isinstance(workspace_state, dict):
        status = workspace_composer_attachment_status(workspace_state, composer_ids)
        if expected == "target":
            ui_ok = all(status.values())
            print(f"Workspace UI selected: {status['selectedComposerIds']}")
            print(f"Workspace UI focused: {status['lastFocusedComposerIds']}")
        else:
            added = workspace_state.get("added", {})
            if isinstance(added, dict):
                for field, values in added.items():
                    if not isinstance(field, str) or not isinstance(values, list):
                        ui_ok = False
                        continue
                    if values:
                        status = workspace_composer_attachment_status(
                            workspace_state, values
                        )
                        ui_ok = ui_ok and not status.get(field, False)
            else:
                ui_ok = False
            print(f"Workspace UI detached: {ui_ok}")
    identity_ok = True
    workspace_identity = manifest.get("workspace_identity")
    if isinstance(workspace_identity, dict):
        identity_status = workspace_identity_status(
            args.db,
            workspace_identity,
            composer_ids,
            at_target=expected == "target",
        )
        identity_ok = bool(identity_status["header_ok"]) and bool(
            identity_status["composer_data_ok"]
        )
        print(
            f"Embedded header identity: {identity_status['header_count']}/"
            f"{identity_status['header_expected']}"
        )
        print(
            f"Embedded composer data identity: "
            f"{identity_status['composer_data_count']}/"
            f"{identity_status['composer_data_expected']}"
        )
    if expected_count == len(composer_ids) and ui_ok and identity_ok:
        print(f"Verification: OK (all recorded headers are at the {expected})")
        return 0
    print(
        f"Verification: FAILED (expected recorded headers at the {expected} "
        "with matching embedded identity and workspace UI state)"
    )
    return 1


def cmd_undo(args: argparse.Namespace) -> int:
    operation_dir, manifest = load_manifest(_backup_root(args), args.operation_id)
    if manifest.get("status") == "undone":
        raise SystemExit("This operation is already marked undone.")
    if manifest.get("status") != "applied":
        raise SystemExit(f"Operation is not undoable in status: {manifest.get('status')}")

    composer_ids, source, target, at_target, at_source, conflicts = _location_counts(
        args.db, manifest
    )
    print(f"Operation: {args.operation_id}")
    print(f"Recorded headers: {len(composer_ids)}")
    print(f"Currently at target: {at_target}")
    print(f"Already at source: {at_source}")
    print(f"Missing or elsewhere: {conflicts}")
    if not args.apply:
        print("\nDry run only. Add --apply after quitting Cursor.")
        return 0
    if at_target != len(composer_ids):
        raise SystemExit("The recorded headers have changed; refusing a partial undo.")
    require_cursor_stopped(confirmed=args.confirm_cursor_quit)
    workspace_state = manifest.get("workspace_state")
    workspace_identity = manifest.get("workspace_identity")
    if not isinstance(workspace_identity, dict):
        workspace_identity = None
    if isinstance(workspace_state, dict):
        updated = undo_headers_with_workspace_state(
            args.db,
            source,
            target,
            composer_ids,
            workspace_state,
            workspace_identity,
        )
    else:
        updated = undo_headers(
            args.db,
            source,
            target,
            composer_ids,
            workspace_identity,
        )
    manifest["status"] = "undone"
    manifest["undo"] = {"status": "applied", "applied_at": utc_now(), "updated_headers": updated}
    save_manifest(operation_dir, manifest)
    print(f"Undone headers: {updated}")
    if workspace_identity is not None:
        print("Restored original embedded workspace identities.")
    if isinstance(workspace_state, dict):
        print("Removed operation-added composers from target workspace UI state.")
    return 0


def cmd_delete_backup(args: argparse.Namespace) -> int:
    operation_dir, manifest = load_manifest(_backup_root(args), args.operation_id)
    state = snapshot_state(operation_dir, manifest)
    present = state == "present"
    bytes_used = snapshot_disk_bytes(operation_dir, manifest)
    print(f"Operation: {args.operation_id}")
    print(f"Snapshot state: {state}")
    print(f"Allocated blocks reported: {bytes_used:,} bytes")
    print("The small manifest and logical undo record will be retained.")
    if not args.apply:
        print("\nDry run only. Add --apply to delete the snapshot files.")
        return 0
    if not present:
        print("Nothing to delete.")
        return 0
    removed = delete_snapshot(operation_dir, manifest)
    print(f"Snapshot files deleted; prior reported allocation was {removed:,} bytes.")
    return 0


def cmd_cleanup(args: argparse.Namespace) -> int:
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.older_than)
    records = list_manifests(_backup_root(args))
    candidates = []
    kept_present = 0
    for operation_dir, manifest in records:
        if not manifest.get("snapshot", {}).get("present"):
            continue
        if kept_present < args.keep_last:
            kept_present += 1
            continue
        try:
            created = datetime.fromisoformat(str(manifest["created_at"]))
        except (KeyError, ValueError):
            continue
        if created <= cutoff and manifest.get("status") in {"applied", "undone"}:
            candidates.append((operation_dir, manifest))

    total = sum(snapshot_disk_bytes(path, record) for path, record in candidates)
    print(f"Snapshots eligible: {len(candidates)}")
    print(f"Allocated blocks reported: {total:,} bytes")
    for _, manifest in candidates:
        print(f"  {manifest['operation_id']}  {manifest['created_at']}  {manifest['status']}")
    if not args.apply:
        print("\nDry run only. Add --apply to delete these snapshot files.")
        return 0
    for operation_dir, manifest in candidates:
        delete_snapshot(operation_dir, manifest)
    print(f"Deleted {len(candidates)} snapshot(s); manifests were retained.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="restore-cursor",
        description="Inspect and safely re-associate Cursor chat history.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=cursor_db_path_from_env(),
        help="Path to Cursor globalStorage/state.vscdb. Defaults to the macOS Cursor DB.",
    )
    parser.add_argument(
        "--backup-dir",
        type=Path,
        help="Managed backup directory. Defaults beside state.vscdb.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="Check that the Cursor DB is readable.").set_defaults(
        func=cmd_doctor
    )

    workspaces = sub.add_parser("workspaces", help="List known Cursor workspace IDs.")
    workspaces.add_argument("filter", nargs="?", help="Optional path or ID filter.")
    workspaces.set_defaults(func=cmd_workspaces)

    project = sub.add_parser(
        "project", help="Resolve workspace IDs and header counts from a project folder."
    )
    project.add_argument("project_path", type=Path, nargs="?", default=Path.cwd())
    project.set_defaults(func=cmd_project)

    recover = sub.add_parser(
        "recover", help="Select and restore chat history starting from a project folder."
    )
    recover.add_argument("project_path", type=Path, nargs="?", default=Path.cwd())
    recover.add_argument(
        "--source",
        help="Source option number, workspace ID/prefix, or old folder path.",
    )
    recover.add_argument(
        "--top-level-only",
        action="store_true",
        help="Select main/top-level composer headers and leave subagents in place.",
    )
    recover.add_argument(
        "--nonempty",
        action="store_true",
        help="Select only composer headers that have at least one stored bubble.",
    )
    recover.add_argument("--apply", action="store_true", help="Actually update the DB.")
    recover.add_argument(
        "--confirm-cursor-quit",
        action="store_true",
        help="Assert Cursor is quit if automatic process detection is unavailable.",
    )
    recover.set_defaults(func=cmd_recover)

    recover_chat = sub.add_parser(
        "recover-chat",
        help="Recover exactly one non-empty, top-level chat into a project workspace.",
    )
    recover_chat.add_argument("project_path", type=Path)
    recover_chat.add_argument("composer", help="Full composer ID or unique ID prefix.")
    recover_chat.add_argument(
        "--apply", action="store_true", help="Actually update the one header."
    )
    recover_chat.add_argument(
        "--confirm-cursor-quit",
        action="store_true",
        help="Assert Cursor is quit if automatic process detection is unavailable.",
    )
    recover_chat.set_defaults(func=cmd_recover_chat)

    search = sub.add_parser("search", help="Search chat header titles.")
    search.add_argument("terms", nargs="*", help="Terms that must appear in the title.")
    search.add_argument("--limit", type=int, default=80)
    search.set_defaults(func=cmd_search)

    inspect = sub.add_parser("inspect", help="Inspect chats for one workspace ID.")
    inspect.add_argument("workspace_id")
    inspect.add_argument(
        "--with-bodies",
        action="store_true",
        help="Count composerData and bubble records. Slower on very large DBs.",
    )
    inspect.add_argument(
        "--sort",
        choices=("activity", "header"),
        help="Date to sort by. Defaults to activity with bodies, otherwise header.",
    )
    inspect.add_argument(
        "--order",
        choices=("newest", "oldest"),
        default="newest",
        help="Sort direction. Defaults to newest.",
    )
    inspect.set_defaults(func=cmd_inspect)

    chat = sub.add_parser(
        "chat", help="Preview one composer, its relationships, and readable transcript."
    )
    chat.add_argument("composer", help="Full composer ID or unique ID prefix.")
    chat.add_argument(
        "--messages",
        type=nonnegative_int,
        default=20,
        metavar="COUNT",
        help="Maximum readable messages to show; use 0 for metadata only.",
    )
    chat.add_argument(
        "--tail",
        action="store_true",
        help="Show the last readable messages instead of the first.",
    )
    chat.add_argument(
        "--max-chars",
        type=positive_int,
        default=1200,
        metavar="COUNT",
        help="Maximum characters shown for each message.",
    )
    chat.set_defaults(func=cmd_chat)

    restore = sub.add_parser(
        "restore",
        help="Dry-run or apply a complete chat workspace reassignment.",
    )
    restore.add_argument("--source", required=True, help="Old workspace ID containing chats.")
    restore.add_argument("--target", required=True, help="Current workspace ID to show chats under.")
    restore.add_argument(
        "--top-level-only",
        action="store_true",
        help="Select main/top-level composer headers and leave subagents in place.",
    )
    restore.add_argument(
        "--nonempty",
        action="store_true",
        help="Select only composer headers that have at least one stored bubble.",
    )
    restore.add_argument("--apply", action="store_true", help="Actually update the DB.")
    restore.add_argument(
        "--allow-unknown-target",
        action="store_true",
        help="Allow a target absent from Cursor workspace metadata.",
    )
    restore.add_argument(
        "--confirm-cursor-quit",
        action="store_true",
        help="Assert Cursor is quit if automatic process detection is unavailable.",
    )
    restore.set_defaults(func=cmd_restore)

    sub.add_parser("backups", help="List managed restore operations and snapshots.").set_defaults(
        func=cmd_backups
    )

    verify = sub.add_parser("verify", help="Verify all chats recorded by an operation.")
    verify.add_argument("operation_id")
    verify.set_defaults(func=cmd_verify)

    undo = sub.add_parser("undo", help="Dry-run or undo one recorded restore operation.")
    undo.add_argument("operation_id")
    undo.add_argument("--apply", action="store_true", help="Actually undo the restore.")
    undo.add_argument(
        "--confirm-cursor-quit",
        action="store_true",
        help="Assert Cursor is quit if automatic process detection is unavailable.",
    )
    undo.set_defaults(func=cmd_undo)

    delete_backup = sub.add_parser(
        "delete-backup",
        help="Delete one emergency snapshot but retain its logical undo manifest.",
    )
    delete_backup.add_argument("operation_id")
    delete_backup.add_argument("--apply", action="store_true")
    delete_backup.set_defaults(func=cmd_delete_backup)

    cleanup = sub.add_parser(
        "cleanup", help="Delete old managed snapshots while retaining manifests."
    )
    cleanup.add_argument("--older-than", type=nonnegative_int, default=30, metavar="DAYS")
    cleanup.add_argument("--keep-last", type=nonnegative_int, default=2, metavar="COUNT")
    cleanup.add_argument("--apply", action="store_true")
    cleanup.set_defaults(func=cmd_cleanup)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (sqlite3.Error, OSError, RuntimeError, ValueError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
