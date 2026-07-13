import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from restore_cursor.cursor_db import (
    composer_messages,
    composer_preview,
    connect_readonly,
    current_project_workspace,
    header_workspace_map,
    normalize_repo_url,
    project_workspaces,
    prepare_workspace_composer_attachment,
    prepare_workspace_identity_rewrite,
    resolve_composer_id,
    restore_headers,
    restore_headers_with_workspace_state,
    sqlite_uri,
    undo_headers,
    undo_headers_with_workspace_state,
    workspace_composer_attachment_status,
    workspace_identity_status,
    workspace_header_summary,
    workspace_headers,
    workspace_selected_header_ids,
)
from restore_cursor.operations import (
    create_restore_operation,
    delete_snapshot,
    load_manifest,
    snapshot_disk_bytes,
)


class SqliteUriTests(unittest.TestCase):
    def test_sqlite_uri_escapes_spaces_for_immutable_mode(self) -> None:
        uri = sqlite_uri(Path("/tmp/Application Support/state.vscdb"), immutable=True)

        self.assertEqual(
            uri,
            "file:/tmp/Application%20Support/state.vscdb?mode=ro&immutable=1",
        )

    def test_sqlite_uri_can_be_plain_file_uri(self) -> None:
        uri = sqlite_uri(Path("/tmp/state.vscdb"), immutable=False)

        self.assertEqual(uri, "file:/tmp/state.vscdb")

    def test_repo_urls_normalize_to_the_same_identity(self) -> None:
        expected = "github.com/amirharati/workbench_agent"

        self.assertEqual(normalize_repo_url("git@github.com:amirharati/workbench_agent.git"), expected)
        self.assertEqual(normalize_repo_url("https://github.com/amirharati/workbench_agent.git"), expected)
        self.assertEqual(normalize_repo_url(expected), expected)


class RestoreLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.db_path = self.root / "state.vscdb"
        conn = sqlite3.connect(self.db_path)
        conn.executescript(
            """
            create table composerHeaders (
                composerId text primary key,
                workspaceId text not null,
                value text,
                isArchived integer,
                isSubagent integer
            );
            create table cursorDiskKV (key text primary key, value blob);
            create table ItemTable (key text primary key, value text);
            """
        )
        conn.executemany(
            "insert into composerHeaders values (?, ?, '{}', 0, 0)",
            [("chat-1", "old"), ("chat-2", "old"), ("existing", "new")],
        )
        self.parent_id = "aaaaaaaa-0000-0000-0000-000000000001"
        self.other_id = "aaaaaaaa-0000-0000-0000-000000000002"
        self.child_id = "bbbbbbbb-0000-0000-0000-000000000001"
        conn.executemany(
            "insert into composerHeaders values (?, ?, ?, 0, ?)",
            [
                (
                    self.parent_id,
                    "old",
                    json.dumps(
                        {
                            "name": "Main session",
                            "unifiedMode": "agent",
                            "createdAt": 1767268800000,
                            "lastUpdatedAt": 1767268800000,
                        }
                    ),
                    0,
                ),
                (self.other_id, "old", json.dumps({"name": "Other session"}), 0),
                (
                    self.child_id,
                    "new",
                    json.dumps({"name": "Explore files", "unifiedMode": "agent"}),
                    1,
                ),
            ],
        )
        conn.executemany(
            "insert into cursorDiskKV values (?, ?)",
            [
                (
                    f"composerData:{self.parent_id}",
                    json.dumps(
                        {
                            "subagentComposerIds": [self.child_id],
                            "lastUpdatedAt": 1782907200000,
                            "conversationCheckpointLastUpdatedAt": 1782907260000,
                        }
                    ),
                ),
                (
                    f"composerData:{self.child_id}",
                    json.dumps(
                        {
                            "subagentInfo": {
                                "parentComposerId": self.parent_id,
                                "subagentTypeName": "explore",
                            }
                        }
                    ),
                ),
                (
                    f"bubbleId:{self.parent_id}:bubble-1",
                    json.dumps(
                        {
                            "bubbleId": "bubble-1",
                            "type": 1,
                            "createdAt": "2026-01-01T00:00:00Z",
                            "text": "Please inspect the project.",
                        }
                    ),
                ),
                (
                    f"bubbleId:{self.parent_id}:bubble-2",
                    json.dumps(
                        {
                            "bubbleId": "bubble-2",
                            "type": 2,
                            "createdAt": "2026-01-01T00:00:01Z",
                            "text": "I found the relevant files.",
                        }
                    ),
                ),
                (
                    f"bubbleId:{self.parent_id}:bubble-3",
                    json.dumps(
                        {
                            "bubbleId": "bubble-3",
                            "type": 2,
                            "createdAt": "2026-01-01T00:00:02Z",
                            "text": "",
                        }
                    ),
                ),
            ],
        )
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_restore_and_exact_undo_leave_existing_target_chat_alone(self) -> None:
        composer_ids = ["chat-1", "chat-2"]

        self.assertEqual(
            restore_headers(
                self.db_path, "old", "new", composer_ids=composer_ids
            ),
            2,
        )
        self.assertEqual(undo_headers(self.db_path, "old", "new", composer_ids), 2)

        conn = connect_readonly(self.db_path)
        try:
            locations = header_workspace_map(conn, composer_ids + ["existing"])
        finally:
            conn.close()
        self.assertEqual(
            locations,
            {"chat-1": "old", "chat-2": "old", "existing": "new"},
        )

    def test_selective_restore_moves_only_one_main_chat(self) -> None:
        self.assertEqual(
            restore_headers(
                self.db_path,
                "old",
                "new",
                composer_ids=[self.parent_id],
            ),
            1,
        )

        conn = connect_readonly(self.db_path)
        try:
            locations = header_workspace_map(
                conn, [self.parent_id, self.other_id, self.child_id]
            )
        finally:
            conn.close()
        self.assertEqual(locations[self.parent_id], "new")
        self.assertEqual(locations[self.other_id], "old")
        self.assertEqual(locations[self.child_id], "new")

    def test_selective_restore_attaches_and_undoes_workspace_ui_state(self) -> None:
        conn = sqlite3.connect(self.db_path)
        self.assertEqual(conn.execute("pragma journal_mode=wal").fetchone()[0], "wal")
        conn.close()
        workspace_db = self.root / "target-state.vscdb"
        conn = sqlite3.connect(workspace_db)
        self.assertEqual(conn.execute("pragma journal_mode=wal").fetchone()[0], "wal")
        conn.execute("create table ItemTable (key text primary key, value text)")
        conn.execute(
            "insert into ItemTable values ('composer.composerData', ?)",
            [
                json.dumps(
                    {
                        "selectedComposerIds": ["existing"],
                        "lastFocusedComposerIds": ["existing"],
                        "hasMigratedComposerData": True,
                    },
                    separators=(",", ":"),
                )
            ],
        )
        conn.commit()
        conn.close()

        source_identity = {
            "id": "old",
            "uri": {
                "$mid": 1,
                "fsPath": "/tmp/old-project",
                "external": "file:///tmp/old-project",
                "path": "/tmp/old-project",
                "scheme": "file",
            },
        }
        conn = sqlite3.connect(self.db_path)
        header = json.loads(
            conn.execute(
                "select value from composerHeaders where composerId = ?",
                [self.parent_id],
            ).fetchone()[0]
        )
        header["workspaceIdentifier"] = source_identity
        body = json.loads(
            conn.execute(
                "select value from cursorDiskKV where key = ?",
                [f"composerData:{self.parent_id}"],
            ).fetchone()[0]
        )
        body["workspaceIdentifier"] = source_identity
        conn.execute(
            "update composerHeaders set value = ? where composerId = ?",
            [json.dumps(header), self.parent_id],
        )
        conn.execute(
            "update cursorDiskKV set value = ? where key = ?",
            [json.dumps(body), f"composerData:{self.parent_id}"],
        )
        conn.commit()
        conn.close()

        plan = prepare_workspace_composer_attachment(
            workspace_db, [self.parent_id]
        )
        identity_plan = prepare_workspace_identity_rewrite(
            self.db_path,
            "old",
            "new",
            "/tmp/new-project",
            [self.parent_id],
        )
        self.assertEqual(
            restore_headers_with_workspace_state(
                self.db_path,
                "old",
                "new",
                [self.parent_id],
                plan,
                identity_plan,
            ),
            1,
        )
        self.assertTrue(
            all(
                workspace_composer_attachment_status(
                    plan, [self.parent_id]
                ).values()
            )
        )

        conn = connect_readonly(self.db_path)
        try:
            locations = header_workspace_map(conn, [self.parent_id, self.other_id])
        finally:
            conn.close()
        self.assertEqual(locations[self.parent_id], "new")
        self.assertEqual(locations[self.other_id], "old")

        self.assertEqual(
            undo_headers_with_workspace_state(
                self.db_path,
                "old",
                "new",
                [self.parent_id],
                plan,
                identity_plan,
            ),
            1,
        )
        status = workspace_composer_attachment_status(plan, [self.parent_id])
        self.assertFalse(any(status.values()))
        conn = connect_readonly(workspace_db)
        try:
            payload = json.loads(
                conn.execute(
                    "select value from ItemTable where key = 'composer.composerData'"
                ).fetchone()["value"]
            )
        finally:
            conn.close()
        self.assertEqual(payload["selectedComposerIds"], ["existing"])
        self.assertEqual(payload["lastFocusedComposerIds"], ["existing"])

    def test_top_level_nonempty_selection_excludes_skeletons_and_subagents(self) -> None:
        conn = connect_readonly(self.db_path)
        try:
            selected = workspace_selected_header_ids(
                conn,
                "old",
                top_level_only=True,
                nonempty=True,
            )
            selected_new = workspace_selected_header_ids(
                conn,
                "new",
                top_level_only=True,
                nonempty=True,
            )
        finally:
            conn.close()

        self.assertEqual(selected, [self.parent_id])
        self.assertEqual(selected_new, [])

    def test_snapshot_can_be_deleted_without_deleting_manifest(self) -> None:
        operation_dir, manifest = create_restore_operation(
            self.db_path,
            self.root / "backups",
            "old",
            "new",
            ["chat-1", "chat-2"],
        )
        operation_id = manifest["operation_id"]

        self.assertTrue(manifest["snapshot"]["present"])
        self.assertGreater(snapshot_disk_bytes(operation_dir, manifest), 0)
        delete_snapshot(operation_dir, manifest)

        _, reloaded = load_manifest(self.root / "backups", operation_id)
        self.assertFalse(reloaded["snapshot"]["present"])
        self.assertTrue((operation_dir / "manifest.json").is_file())

    def test_operation_snapshots_target_workspace_state(self) -> None:
        workspace_db = self.root / "target-state.vscdb"
        workspace_db.write_bytes(b"target workspace state")
        plan = {
            "database": str(workspace_db),
            "key": "composer.composerData",
            "previous_value": "{}",
            "next_value": "{}",
            "added": {},
        }

        operation_dir, manifest = create_restore_operation(
            self.db_path,
            self.root / "backups-with-workspace",
            "old",
            "new",
            [self.parent_id],
            workspace_state=plan,
        )

        self.assertEqual(manifest["schema_version"], 3)
        self.assertEqual(manifest["workspace_state"], plan)
        self.assertTrue((operation_dir / "workspace-state.vscdb.bak").is_file())

    def test_restore_and_undo_embedded_workspace_identities(self) -> None:
        source_identity = {
            "id": "old",
            "uri": {
                "$mid": 1,
                "fsPath": "/tmp/old-project",
                "external": "file:///tmp/old-project",
                "path": "/tmp/old-project",
                "scheme": "file",
            },
        }
        conn = sqlite3.connect(self.db_path)
        header = json.loads(
            conn.execute(
                "select value from composerHeaders where composerId = ?",
                [self.parent_id],
            ).fetchone()[0]
        )
        header["workspaceIdentifier"] = source_identity
        body = json.loads(
            conn.execute(
                "select value from cursorDiskKV where key = ?",
                [f"composerData:{self.parent_id}"],
            ).fetchone()[0]
        )
        body["workspaceIdentifier"] = source_identity
        conn.execute(
            "update composerHeaders set value = ? where composerId = ?",
            [json.dumps(header), self.parent_id],
        )
        conn.execute(
            "update cursorDiskKV set value = ? where key = ?",
            [json.dumps(body), f"composerData:{self.parent_id}"],
        )
        conn.commit()
        conn.close()

        identity_plan = prepare_workspace_identity_rewrite(
            self.db_path,
            "old",
            "new",
            "/tmp/new-project",
            [self.parent_id],
        )
        self.assertEqual(
            restore_headers(
                self.db_path,
                "old",
                "new",
                composer_ids=[self.parent_id],
                workspace_identity=identity_plan,
            ),
            1,
        )
        status = workspace_identity_status(
            self.db_path, identity_plan, [self.parent_id], at_target=True
        )
        self.assertTrue(status["header_ok"])
        self.assertTrue(status["composer_data_ok"])

        self.assertEqual(
            undo_headers(
                self.db_path,
                "old",
                "new",
                [self.parent_id],
                identity_plan,
            ),
            1,
        )
        status = workspace_identity_status(
            self.db_path, identity_plan, [self.parent_id], at_target=False
        )
        self.assertTrue(status["header_ok"])
        self.assertTrue(status["composer_data_ok"])

    def test_chat_preview_links_parent_child_across_workspaces(self) -> None:
        conn = connect_readonly(self.db_path)
        try:
            parent = composer_preview(conn, self.parent_id)
            child = composer_preview(conn, self.child_id)
            messages = composer_messages(
                conn, self.parent_id, limit=10, tail=False
            )
        finally:
            conn.close()

        self.assertEqual(parent["children"][0]["composerId"], self.child_id)
        self.assertEqual(child["parent"]["composerId"], self.parent_id)
        self.assertEqual(child["subagent_type"], "explore")
        self.assertEqual(parent["bubbles"], 3)
        self.assertEqual(parent["text_messages"], 2)
        self.assertEqual([row["type"] for row in messages], [1, 2])

    def test_workspace_body_inspection_counts_readable_text(self) -> None:
        conn = connect_readonly(self.db_path)
        try:
            rows = workspace_headers(
                conn,
                "old",
                with_bodies=True,
                sort_by="activity",
                newest_first=True,
            )
        finally:
            conn.close()

        parent = next(row for row in rows if row["composerId"] == self.parent_id)
        self.assertEqual(rows[0]["composerId"], self.parent_id)
        self.assertGreater(parent["activity"], parent["updated"])
        self.assertEqual(parent["bubbles"], 3)
        self.assertEqual(parent["text_messages"], 2)
        self.assertGreater(parent["bubble_bytes"], 0)

    def test_workspace_activity_can_sort_oldest_first(self) -> None:
        conn = connect_readonly(self.db_path)
        try:
            rows = workspace_headers(
                conn,
                "old",
                with_bodies=False,
                sort_by="activity",
                newest_first=False,
            )
        finally:
            conn.close()

        self.assertEqual(rows[-1]["composerId"], self.parent_id)

    def test_workspace_summary_separates_stale_header_from_main_activity(self) -> None:
        conn = connect_readonly(self.db_path)
        try:
            summary = workspace_header_summary(conn, "old")
        finally:
            conn.close()

        headers, top_level, subagents, latest_header, main_activity, any_activity = summary
        self.assertEqual((headers, top_level, subagents), (4, 4, 0))
        self.assertLess(latest_header, main_activity)
        self.assertEqual(main_activity, any_activity)

    def test_composer_prefix_must_be_unique(self) -> None:
        conn = connect_readonly(self.db_path)
        try:
            self.assertEqual(
                resolve_composer_id(conn, self.child_id[:8]), self.child_id
            )
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                resolve_composer_id(conn, "aaaaaaaa")
        finally:
            conn.close()


class ProjectResolutionTests(unittest.TestCase):
    def test_folder_resolves_current_target_and_same_repo_history(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            project = root / "project"
            old_copy = root / "project-old"
            storage = root / "workspaceStorage"
            project.mkdir()
            old_copy.mkdir()
            for workspace_id, path, modified in [
                ("current", project, 200),
                ("older-same-path", project, 100),
                ("old-copy", old_copy, 50),
            ]:
                workspace_dir = storage / workspace_id
                workspace_dir.mkdir(parents=True)
                (workspace_dir / "workspace.json").write_text(
                    json.dumps({"folder": path.as_uri()})
                )
                os.utime(workspace_dir, (modified, modified))

            db_path = root / "state.vscdb"
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            conn.execute("create table ItemTable (key text primary key, value text)")
            entries = {
                "entries": [
                    {
                        "workspaceId": workspace_id,
                        "displayPath": str(path),
                        "trackedGitRepos": [
                            {"repoUrl": "github.com/amirharati/project"}
                        ],
                    }
                    for workspace_id, path in [
                        ("current", project),
                        ("older-same-path", project),
                        ("old-copy", old_copy),
                    ]
                ]
            }
            conn.execute(
                "insert into ItemTable values ('workspaceMetadata.entries', ?)",
                [json.dumps(entries)],
            )
            conn.commit()

            with patch(
                "restore_cursor.cursor_db.project_git_remote",
                return_value="github.com/amirharati/project",
            ):
                matches = project_workspaces(conn, project, storage)
            conn.close()

            relations = {
                match.workspace.workspace_id: match.relation for match in matches
            }
            self.assertEqual(relations["current"], "exact-path")
            self.assertEqual(relations["older-same-path"], "exact-path")
            self.assertEqual(relations["old-copy"], "same-repository")
            self.assertEqual(
                current_project_workspace(matches).workspace.workspace_id,
                "current",
            )


if __name__ == "__main__":
    unittest.main()
