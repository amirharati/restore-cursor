from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import string
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse
from urllib.parse import quote


DEFAULT_DB = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Cursor"
    / "User"
    / "globalStorage"
    / "state.vscdb"
)

DEFAULT_WORKSPACE_STORAGE = (
    Path.home()
    / "Library"
    / "Application Support"
    / "Cursor"
    / "User"
    / "workspaceStorage"
)

WORKSPACE_COMPOSER_STATE_KEY = "composer.composerData"


@dataclass(frozen=True)
class WorkspaceInfo:
    workspace_id: str
    path: str
    source: str
    repo_urls: tuple[str, ...] = ()
    storage_modified: float | None = None


@dataclass(frozen=True)
class ProjectWorkspace:
    workspace: WorkspaceInfo
    relation: str


def sqlite_uri(db_path: Path, *, immutable: bool) -> str:
    uri = "file:" + quote(str(db_path), safe="/:")
    if immutable:
        return uri + "?mode=ro&immutable=1"
    return uri


def connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(sqlite_uri(db_path, immutable=True), uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def connect_write(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def workspace_state_db_path(
    workspace_id: str,
    workspace_storage: Path = DEFAULT_WORKSPACE_STORAGE,
) -> Path:
    return workspace_storage / workspace_id / "state.vscdb"


def prepare_workspace_composer_attachment(
    db_path: Path, composer_ids: list[str]
) -> dict[str, object]:
    if not db_path.is_file():
        raise FileNotFoundError(f"Target workspace state DB not found: {db_path}")
    conn = connect_readonly(db_path)
    try:
        row = conn.execute(
            "select value from ItemTable where key = ?",
            [WORKSPACE_COMPOSER_STATE_KEY],
        ).fetchone()
    finally:
        conn.close()
    if row is None or not isinstance(row["value"], str):
        raise RuntimeError(
            f"Target workspace is missing {WORKSPACE_COMPOSER_STATE_KEY}."
        )
    previous_value = str(row["value"])
    try:
        payload = json.loads(previous_value)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Target workspace composer state is not valid JSON.") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("Target workspace composer state is not a JSON object.")

    added: dict[str, list[str]] = {}
    for field in ("selectedComposerIds", "lastFocusedComposerIds"):
        current = payload.get(field, [])
        if not isinstance(current, list) or any(
            not isinstance(value, str) for value in current
        ):
            raise RuntimeError(f"Target workspace composer state has invalid {field}.")
        additions = [composer_id for composer_id in composer_ids if composer_id not in current]
        payload[field] = additions + current
        added[field] = additions

    return {
        "database": str(db_path),
        "key": WORKSPACE_COMPOSER_STATE_KEY,
        "previous_value": previous_value,
        "next_value": json.dumps(payload, separators=(",", ":")),
        "added": added,
    }


def workspace_composer_attachment_status(
    plan: dict[str, object], composer_ids: list[str]
) -> dict[str, bool]:
    db_path = Path(str(plan["database"]))
    conn = connect_readonly(db_path)
    try:
        row = conn.execute(
            "select value from ItemTable where key = ?", [str(plan["key"])]
        ).fetchone()
    finally:
        conn.close()
    if row is None:
        return {field: False for field in ("selectedComposerIds", "lastFocusedComposerIds")}
    try:
        payload = json.loads(str(row["value"]))
    except json.JSONDecodeError:
        return {field: False for field in ("selectedComposerIds", "lastFocusedComposerIds")}
    if not isinstance(payload, dict):
        return {field: False for field in ("selectedComposerIds", "lastFocusedComposerIds")}
    return {
        field: isinstance(payload.get(field), list)
        and all(composer_id in payload[field] for composer_id in composer_ids)
        for field in ("selectedComposerIds", "lastFocusedComposerIds")
    }


def ensure_schema(conn: sqlite3.Connection) -> None:
    tables = {
        row["name"]
        for row in conn.execute(
            "select name from sqlite_master where type = 'table'"
        )
    }
    missing = {"composerHeaders", "cursorDiskKV", "ItemTable"} - tables
    if missing:
        raise RuntimeError(f"Missing expected table(s): {', '.join(sorted(missing))}")


def load_workspace_storage_paths(root: Path = DEFAULT_WORKSPACE_STORAGE) -> dict[str, str]:
    paths: dict[str, str] = {}
    if not root.exists():
        return paths
    for workspace_json in root.glob("*/workspace.json"):
        workspace_id = workspace_json.parent.name
        try:
            payload = json.loads(workspace_json.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict):
            continue
        folder = payload.get("folder") or payload.get("workspace")
        if isinstance(folder, str):
            paths[workspace_id] = folder
    return paths


def load_workspace_storage_info(
    root: Path = DEFAULT_WORKSPACE_STORAGE,
) -> dict[str, WorkspaceInfo]:
    result: dict[str, WorkspaceInfo] = {}
    for workspace_id, path in load_workspace_storage_paths(root).items():
        workspace_dir = root / workspace_id
        try:
            modified = workspace_dir.stat().st_mtime
        except OSError:
            modified = None
        result[workspace_id] = WorkspaceInfo(
            workspace_id=workspace_id,
            path=path,
            source="workspaceStorage",
            storage_modified=modified,
        )
    return result


def load_workspace_metadata(conn: sqlite3.Connection) -> dict[str, str]:
    return {
        workspace_id: info.path
        for workspace_id, info in load_workspace_metadata_info(conn).items()
    }


def load_workspace_metadata_info(
    conn: sqlite3.Connection,
) -> dict[str, WorkspaceInfo]:
    row = conn.execute(
        "select value from ItemTable where key = 'workspaceMetadata.entries'"
    ).fetchone()
    if not row:
        return {}
    try:
        payload = json.loads(row["value"])
    except (TypeError, json.JSONDecodeError):
        return {}

    if isinstance(payload, dict):
        entries = payload.get("entries", [])
    elif isinstance(payload, list):
        entries = payload
    else:
        entries = []
    workspaces: dict[str, WorkspaceInfo] = {}
    if not isinstance(entries, list):
        return workspaces
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        workspace_id = entry.get("workspaceId")
        display_path = entry.get("displayPath") or entry.get("folderUri")
        if isinstance(workspace_id, str) and isinstance(display_path, str):
            repo_urls = []
            tracked_repos = entry.get("trackedGitRepos", [])
            if isinstance(tracked_repos, list):
                for repo in tracked_repos:
                    if isinstance(repo, dict) and isinstance(repo.get("repoUrl"), str):
                        repo_urls.append(repo["repoUrl"])
            workspaces[workspace_id] = WorkspaceInfo(
                workspace_id=workspace_id,
                path=display_path,
                source="workspaceMetadata",
                repo_urls=tuple(repo_urls),
            )
    return workspaces


def list_workspaces(
    conn: sqlite3.Connection,
    workspace_storage: Path = DEFAULT_WORKSPACE_STORAGE,
) -> list[WorkspaceInfo]:
    by_id = load_workspace_storage_info(workspace_storage)
    for workspace_id, metadata in load_workspace_metadata_info(conn).items():
        storage = by_id.get(workspace_id)
        by_id[workspace_id] = WorkspaceInfo(
            workspace_id=workspace_id,
            path=metadata.path,
            source=(
                "workspaceStorage+workspaceMetadata"
                if storage is not None
                else "workspaceMetadata"
            ),
            repo_urls=metadata.repo_urls,
            storage_modified=storage.storage_modified if storage else None,
        )
    return sorted(by_id.values(), key=lambda item: (item.path.lower(), item.workspace_id))


def normalize_workspace_path(value: str | Path) -> str:
    raw = str(value)
    if raw.startswith("file://"):
        raw = unquote(urlparse(raw).path)
    path = Path(raw).expanduser()
    return str(path.resolve(strict=False)).rstrip("/")


def normalize_repo_url(value: str) -> str:
    normalized = value.strip()
    if normalized.startswith("git@") and ":" in normalized:
        host, path = normalized[4:].split(":", 1)
        normalized = f"{host}/{path}"
    elif "://" in normalized:
        parsed = urlparse(normalized)
        host = parsed.hostname or ""
        normalized = f"{host}{parsed.path}"
    normalized = normalized.removeprefix("git@").removesuffix(".git")
    return normalized.strip("/").lower()


def project_git_remote(project_path: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(project_path), "config", "--get", "remote.origin.url"],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        return None
    value = result.stdout.strip()
    return normalize_repo_url(value) if result.returncode == 0 and value else None


def project_workspaces(
    conn: sqlite3.Connection,
    project_path: Path,
    workspace_storage: Path = DEFAULT_WORKSPACE_STORAGE,
) -> list[ProjectWorkspace]:
    exact_path = normalize_workspace_path(project_path)
    project_repo = project_git_remote(project_path)
    matches: list[ProjectWorkspace] = []
    for workspace in list_workspaces(conn, workspace_storage):
        workspace_path = normalize_workspace_path(workspace.path)
        workspace_repos = {normalize_repo_url(url) for url in workspace.repo_urls}
        if workspace_path == exact_path:
            matches.append(ProjectWorkspace(workspace, "exact-path"))
        elif project_repo and project_repo in workspace_repos:
            matches.append(ProjectWorkspace(workspace, "same-repository"))
    return sorted(
        matches,
        key=lambda item: (
            item.relation != "exact-path",
            -(item.workspace.storage_modified or 0),
            item.workspace.workspace_id,
        ),
    )


def current_project_workspace(matches: list[ProjectWorkspace]) -> ProjectWorkspace | None:
    exact = [
        item
        for item in matches
        if item.relation == "exact-path"
        and item.workspace.storage_modified is not None
    ]
    if not exact:
        return None
    return max(
        exact,
        key=lambda item: (
            item.workspace.storage_modified is not None,
            item.workspace.storage_modified or 0,
        ),
    )


def workspace_header_summary(
    conn: sqlite3.Connection, workspace_id: str
) -> tuple[int, int, int, str | None, str | None, str | None]:
    row = conn.execute(
        """
        with headers as (
          select
            h.isSubagent,
            coalesce(
              json_extract(h.value, '$.lastUpdatedAt'),
              json_extract(h.value, '$.createdAt')
            ) as header_ms,
            nullif(
              max(
                coalesce(json_extract(cd.value, '$.conversationCheckpointLastUpdatedAt'), 0),
                coalesce(json_extract(cd.value, '$.lastUpdatedAt'), 0),
                coalesce(json_extract(cd.value, '$.createdAt'), 0)
              ),
              0
            ) as activity_ms
          from composerHeaders h
          left join cursorDiskKV cd on cd.key = 'composerData:' || h.composerId
          where h.workspaceId = ?
        )
        select
          count(*) as n,
          sum(case when coalesce(isSubagent, 0) = 0 then 1 else 0 end) as top_level,
          sum(case when isSubagent = 1 then 1 else 0 end) as subagents,
          datetime(max(header_ms) / 1000, 'unixepoch', 'localtime') as latest_header,
          datetime(
            max(case when coalesce(isSubagent, 0) = 0 then activity_ms end) / 1000,
            'unixepoch',
            'localtime'
          ) as main_activity,
          datetime(max(activity_ms) / 1000, 'unixepoch', 'localtime') as any_activity
        from headers
        """,
        [workspace_id],
    ).fetchone()
    return (
        int(row["n"]),
        int(row["top_level"] or 0),
        int(row["subagents"] or 0),
        row["latest_header"],
        row["main_activity"],
        row["any_activity"],
    )


def resolve_composer_id(conn: sqlite3.Connection, selector: str) -> str:
    selector = selector.strip().lower()
    allowed = set(string.hexdigits.lower() + "-")
    if not selector or any(char not in allowed for char in selector):
        raise ValueError("Composer ID or prefix must contain only hexadecimal characters and hyphens.")

    exact = conn.execute(
        "select composerId from composerHeaders where lower(composerId) = ?",
        [selector],
    ).fetchone()
    if exact:
        return str(exact["composerId"])

    matches = list(
        conn.execute(
            """
            select composerId
            from composerHeaders
            where lower(composerId) like ?
            order by composerId
            limit 3
            """,
            [selector + "%"],
        )
    )
    if not matches:
        raise ValueError(f"No composer matches: {selector}")
    if len(matches) > 1:
        raise ValueError(
            f"Composer prefix is ambiguous: {selector}. Use more characters."
        )
    return str(matches[0]["composerId"])


def composer_header(conn: sqlite3.Connection, composer_id: str) -> sqlite3.Row | None:
    return conn.execute(
        """
        select
          composerId,
          workspaceId,
          coalesce(json_extract(value, '$.name'), '') as name,
          datetime(json_extract(value, '$.createdAt') / 1000, 'unixepoch', 'localtime') as created,
          datetime(json_extract(value, '$.lastUpdatedAt') / 1000, 'unixepoch', 'localtime') as updated,
          json_extract(value, '$.unifiedMode') as mode,
          isArchived,
          isSubagent,
          length(value) as header_bytes
        from composerHeaders
        where composerId = ?
        """,
        [composer_id],
    ).fetchone()


def composer_preview(conn: sqlite3.Connection, composer_id: str) -> dict[str, object]:
    header = composer_header(conn, composer_id)
    if header is None:
        raise ValueError(f"Composer header not found: {composer_id}")

    data_row = conn.execute(
        "select value, length(value) as n from cursorDiskKV where key = ?",
        [f"composerData:{composer_id}"],
    ).fetchone()
    data: dict[str, object] = {}
    composer_bytes = 0
    if data_row:
        composer_bytes = int(data_row["n"] or 0)
        try:
            parsed = json.loads(data_row["value"])
        except (TypeError, json.JSONDecodeError):
            parsed = {}
        if isinstance(parsed, dict):
            data = parsed

    subagent_info = data.get("subagentInfo")
    if not isinstance(subagent_info, dict):
        subagent_info = {}
    parent_id = subagent_info.get("parentComposerId")
    parent = (
        composer_header(conn, parent_id)
        if isinstance(parent_id, str) and parent_id
        else None
    )

    child_ids: list[str] = []
    for key in ("subagentComposerIds", "subComposerIds"):
        values = data.get(key, [])
        if not isinstance(values, list):
            continue
        for value in values:
            if isinstance(value, str) and value not in child_ids:
                child_ids.append(value)
    children = []
    for child_id in child_ids:
        child = composer_header(conn, child_id)
        if child is not None:
            children.append(child)

    counts = conn.execute(
        """
        select
          count(*) as bubbles,
          sum(
            case
              when length(trim(coalesce(json_extract(value, '$.text'), ''))) > 0
              then 1 else 0
            end
          ) as text_messages
        from cursorDiskKV
        where key >= ? and key < ?
        """,
        [f"bubbleId:{composer_id}:", f"bubbleId:{composer_id};"],
    ).fetchone()

    return {
        "header": header,
        "composer_bytes": composer_bytes,
        "bubbles": int(counts["bubbles"] or 0),
        "text_messages": int(counts["text_messages"] or 0),
        "subagent_type": subagent_info.get("subagentTypeName"),
        "parent": parent,
        "parent_id": parent_id if isinstance(parent_id, str) else None,
        "children": children,
        "missing_child_ids": [
            child_id
            for child_id in child_ids
            if all(str(child["composerId"]) != child_id for child in children)
        ],
    }


def composer_messages(
    conn: sqlite3.Connection,
    composer_id: str,
    *,
    limit: int,
    tail: bool,
) -> list[sqlite3.Row]:
    direction = "desc" if tail else "asc"
    rows = list(
        conn.execute(
            f"""
            select
              json_extract(value, '$.bubbleId') as bubbleId,
              json_extract(value, '$.type') as type,
              json_extract(value, '$.createdAt') as created,
              json_extract(value, '$.text') as text
            from cursorDiskKV
            where key >= ? and key < ?
              and length(trim(coalesce(json_extract(value, '$.text'), ''))) > 0
            order by coalesce(json_extract(value, '$.createdAt'), '') {direction}, key {direction}
            limit ?
            """,
            [f"bubbleId:{composer_id}:", f"bubbleId:{composer_id};", limit],
        )
    )
    return list(reversed(rows)) if tail else rows


def search_headers(conn: sqlite3.Connection, terms: list[str], limit: int) -> list[sqlite3.Row]:
    where = ""
    params: list[object] = []
    if terms:
        clauses = []
        for term in terms:
            clauses.append("lower(coalesce(json_extract(value, '$.name'), '')) like ?")
            params.append(f"%{term.lower()}%")
        where = "where " + " and ".join(clauses)
    params.append(limit)
    return list(
        conn.execute(
            f"""
            select
              composerId,
              workspaceId,
              json_extract(value, '$.name') as name,
              datetime(json_extract(value, '$.createdAt') / 1000, 'unixepoch', 'localtime') as created,
              datetime(json_extract(value, '$.lastUpdatedAt') / 1000, 'unixepoch', 'localtime') as updated,
              json_extract(value, '$.unifiedMode') as mode,
              isArchived,
              isSubagent,
              length(value) as header_bytes
            from composerHeaders
            {where}
            order by coalesce(json_extract(value, '$.lastUpdatedAt'), json_extract(value, '$.createdAt')) desc
            limit ?
            """,
            params,
        )
    )


def workspace_headers(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    with_bodies: bool,
    sort_by: str = "header",
    newest_first: bool = True,
) -> list[sqlite3.Row]:
    if sort_by not in {"activity", "header"}:
        raise ValueError(f"Unsupported workspace header sort: {sort_by}")
    direction = "desc" if newest_first else "asc"
    order_value = (
        "coalesce(activity, updated, created)"
        if sort_by == "activity"
        else "coalesce(updated, created)"
    )
    if with_bodies:
        query = f"""
            with h as (
              select
                ch.composerId,
                ch.workspaceId,
                json_extract(ch.value, '$.name') as name,
                datetime(json_extract(ch.value, '$.createdAt') / 1000, 'unixepoch', 'localtime') as created,
                datetime(json_extract(ch.value, '$.lastUpdatedAt') / 1000, 'unixepoch', 'localtime') as updated,
                datetime(
                  nullif(
                    max(
                      coalesce(json_extract(cd.value, '$.conversationCheckpointLastUpdatedAt'), 0),
                      coalesce(json_extract(cd.value, '$.lastUpdatedAt'), 0),
                      coalesce(json_extract(cd.value, '$.createdAt'), 0)
                    ),
                    0
                  ) / 1000,
                  'unixepoch',
                  'localtime'
                ) as activity,
                json_extract(ch.value, '$.unifiedMode') as mode,
                ch.isArchived,
                ch.isSubagent,
                length(cd.value) as composer_bytes
              from composerHeaders ch
              left join cursorDiskKV cd on cd.key = 'composerData:' || ch.composerId
              where ch.workspaceId = ?
            )
            select
              h.*,
              (
                select count(*)
                from cursorDiskKV b
                where b.key >= 'bubbleId:' || h.composerId || ':'
                  and b.key < 'bubbleId:' || h.composerId || ';'
              ) as bubbles,
              (
                select sum(length(value))
                from cursorDiskKV b
                where b.key >= 'bubbleId:' || h.composerId || ':'
                  and b.key < 'bubbleId:' || h.composerId || ';'
              ) as bubble_bytes,
              (
                select count(*)
                from cursorDiskKV b
                where b.key >= 'bubbleId:' || h.composerId || ':'
                  and b.key < 'bubbleId:' || h.composerId || ';'
                  and length(trim(coalesce(json_extract(b.value, '$.text'), ''))) > 0
              ) as text_messages
            from h
            order by {order_value} {direction}, composerId
        """
    else:
        query = f"""
            select
              ch.composerId,
              ch.workspaceId,
              json_extract(ch.value, '$.name') as name,
              datetime(json_extract(ch.value, '$.createdAt') / 1000, 'unixepoch', 'localtime') as created,
              datetime(json_extract(ch.value, '$.lastUpdatedAt') / 1000, 'unixepoch', 'localtime') as updated,
              datetime(
                nullif(
                  max(
                    coalesce(json_extract(cd.value, '$.conversationCheckpointLastUpdatedAt'), 0),
                    coalesce(json_extract(cd.value, '$.lastUpdatedAt'), 0),
                    coalesce(json_extract(cd.value, '$.createdAt'), 0)
                  ),
                  0
                ) / 1000,
                'unixepoch',
                'localtime'
              ) as activity,
              json_extract(ch.value, '$.unifiedMode') as mode,
              ch.isArchived,
              ch.isSubagent,
              length(ch.value) as header_bytes
            from composerHeaders ch
            left join cursorDiskKV cd on cd.key = 'composerData:' || ch.composerId
            where ch.workspaceId = ?
            order by {order_value} {direction}, ch.composerId
        """
    return list(conn.execute(query, [workspace_id]))


def count_headers(conn: sqlite3.Connection, workspace_id: str) -> int:
    row = conn.execute(
        "select count(*) as n from composerHeaders where workspaceId = ?",
        [workspace_id],
    ).fetchone()
    return int(row["n"])


def workspace_header_ids(conn: sqlite3.Connection, workspace_id: str) -> list[str]:
    return [
        str(row["composerId"])
        for row in conn.execute(
            "select composerId from composerHeaders where workspaceId = ? order by composerId",
            [workspace_id],
        )
    ]


def workspace_selected_header_ids(
    conn: sqlite3.Connection,
    workspace_id: str,
    *,
    top_level_only: bool,
    nonempty: bool,
) -> list[str]:
    conditions = ["h.workspaceId = ?"]
    if top_level_only:
        conditions.append("coalesce(h.isSubagent, 0) = 0")
    if nonempty:
        conditions.append(
            """
            exists (
              select 1
              from cursorDiskKV b
              where b.key >= 'bubbleId:' || h.composerId || ':'
                and b.key < 'bubbleId:' || h.composerId || ';'
            )
            """
        )
    where = " and ".join(conditions)
    return [
        str(row["composerId"])
        for row in conn.execute(
            f"""
            select h.composerId
            from composerHeaders h
            where {where}
            order by h.composerId
            """,
            [workspace_id],
        )
    ]


def header_workspace_map(
    conn: sqlite3.Connection, composer_ids: list[str]
) -> dict[str, str]:
    result: dict[str, str] = {}
    for start in range(0, len(composer_ids), 500):
        chunk = composer_ids[start : start + 500]
        placeholders = ",".join("?" for _ in chunk)
        for row in conn.execute(
            f"select composerId, workspaceId from composerHeaders where composerId in ({placeholders})",
            chunk,
        ):
            result[str(row["composerId"])] = str(row["workspaceId"])
    return result


def _load_ids_table(conn: sqlite3.Connection, composer_ids: list[str]) -> None:
    conn.execute("drop table if exists temp.restore_cursor_ids")
    conn.execute(
        "create temp table restore_cursor_ids (composerId text primary key) without rowid"
    )
    conn.executemany(
        "insert into restore_cursor_ids (composerId) values (?)",
        ((composer_id,) for composer_id in composer_ids),
    )


def workspace_identifier(workspace_id: str, workspace_path: str | Path) -> dict[str, object]:
    path = normalize_workspace_path(workspace_path)
    return {
        "id": workspace_id,
        "uri": {
            "$mid": 1,
            "fsPath": path,
            "external": Path(path).as_uri(),
            "path": path,
            "scheme": "file",
        },
    }


def prepare_workspace_identity_rewrite(
    db_path: Path,
    source: str,
    target: str,
    target_path: str | Path,
    composer_ids: list[str],
) -> dict[str, object]:
    """Capture exact source identities before replacing them with the target identity."""
    conn = connect_readonly(db_path)
    try:
        _load_ids_table(conn, composer_ids)
        header_rows = conn.execute(
            """
            select
              h.composerId,
              json_valid(h.value) as valid,
              json_extract(h.value, '$.workspaceIdentifier') as identity
            from composerHeaders h
            join restore_cursor_ids i on i.composerId = h.composerId
            where h.workspaceId = ?
            """,
            [source],
        ).fetchall()
        if len(header_rows) != len(composer_ids):
            raise RuntimeError(
                "Chat headers changed while preparing the workspace identity rewrite."
            )

        body_rows = conn.execute(
            """
            select
              substr(d.key, length('composerData:') + 1) as composerId,
              json_valid(d.value) as valid,
              json_extract(d.value, '$.workspaceIdentifier') as identity
            from cursorDiskKV d
            join restore_cursor_ids i
              on d.key = 'composerData:' || i.composerId
            """
        ).fetchall()
    finally:
        conn.close()

    originals: dict[str, dict[str, object | None]] = {
        composer_id: {"header": None, "composer_data": None}
        for composer_id in composer_ids
    }
    for row in header_rows:
        if not row["valid"] or not isinstance(row["identity"], str):
            raise RuntimeError(
                f"Composer header {row['composerId']} has no valid workspace identity."
            )
        identity = json.loads(row["identity"])
        if not isinstance(identity, dict) or identity.get("id") != source:
            raise RuntimeError(
                f"Composer header {row['composerId']} does not contain the expected "
                "source workspace identity."
            )
        originals[str(row["composerId"])]["header"] = identity

    for row in body_rows:
        if not row["valid"] or not isinstance(row["identity"], str):
            raise RuntimeError(
                f"Composer data {row['composerId']} has no valid workspace identity."
            )
        identity = json.loads(row["identity"])
        if not isinstance(identity, dict) or identity.get("id") != source:
            raise RuntimeError(
                f"Composer data {row['composerId']} does not contain the expected "
                "source workspace identity."
            )
        originals[str(row["composerId"])]["composer_data"] = identity

    return {
        "target": workspace_identifier(target, target_path),
        "originals": originals,
        "composer_data_count": len(body_rows),
    }


def _identity_json(identity: object) -> str:
    if not isinstance(identity, dict) or not isinstance(identity.get("id"), str):
        raise RuntimeError("Operation has invalid workspace identity metadata.")
    return json.dumps(identity, separators=(",", ":"), sort_keys=True)


def _validate_identity_locations(
    conn: sqlite3.Connection,
    composer_ids: list[str],
    expected_workspace_id: str,
    workspace_identity: dict[str, object],
) -> None:
    _load_ids_table(conn, composer_ids)
    header_count = conn.execute(
        """
        select count(*) as n
        from composerHeaders h
        join restore_cursor_ids i on i.composerId = h.composerId
        where h.workspaceId = ?
          and json_valid(h.value)
          and json_extract(h.value, '$.workspaceIdentifier.id') = ?
        """,
        [expected_workspace_id, expected_workspace_id],
    ).fetchone()["n"]
    if header_count != len(composer_ids):
        raise RuntimeError(
            "Chat headers or embedded workspace identities changed; refusing "
            "a partial operation."
        )

    expected_bodies = int(workspace_identity.get("composer_data_count", 0))
    body_count = conn.execute(
        """
        select count(*) as n
        from cursorDiskKV d
        join restore_cursor_ids i on d.key = 'composerData:' || i.composerId
        where json_valid(d.value)
          and json_extract(d.value, '$.workspaceIdentifier.id') = ?
        """,
        [expected_workspace_id],
    ).fetchone()["n"]
    if body_count != expected_bodies:
        raise RuntimeError(
            "Composer data or embedded workspace identities changed; refusing "
            "a partial operation."
        )


def _apply_target_workspace_identity(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    composer_ids: list[str],
    workspace_identity: dict[str, object],
) -> int:
    _validate_identity_locations(conn, composer_ids, source, workspace_identity)
    target_json = _identity_json(workspace_identity.get("target"))
    cur = conn.execute(
        """
        update composerHeaders
        set workspaceId = ?,
            value = json_set(value, '$.workspaceIdentifier', json(?))
        where workspaceId = ?
          and composerId in (select composerId from restore_cursor_ids)
        """,
        [target, target_json, source],
    )
    body_cur = conn.execute(
        """
        update cursorDiskKV
        set value = json_set(value, '$.workspaceIdentifier', json(?))
        where key in (
          select 'composerData:' || composerId from restore_cursor_ids
        )
        """,
        [target_json],
    )
    if body_cur.rowcount != int(workspace_identity.get("composer_data_count", 0)):
        raise RuntimeError("Not all composer data identities were updated.")
    return cur.rowcount


def _restore_source_workspace_identity(
    conn: sqlite3.Connection,
    source: str,
    target: str,
    composer_ids: list[str],
    workspace_identity: dict[str, object],
) -> int:
    _validate_identity_locations(conn, composer_ids, target, workspace_identity)
    originals = workspace_identity.get("originals")
    if not isinstance(originals, dict) or set(originals) != set(composer_ids):
        raise RuntimeError("Operation has incomplete original workspace identities.")

    updated = 0
    updated_bodies = 0
    for composer_id in composer_ids:
        original = originals.get(composer_id)
        if not isinstance(original, dict):
            raise RuntimeError("Operation has invalid original workspace identities.")
        header_json = _identity_json(original.get("header"))
        cur = conn.execute(
            """
            update composerHeaders
            set workspaceId = ?,
                value = json_set(value, '$.workspaceIdentifier', json(?))
            where workspaceId = ? and composerId = ?
            """,
            [source, header_json, target, composer_id],
        )
        updated += cur.rowcount
        body_identity = original.get("composer_data")
        if body_identity is not None:
            body_cur = conn.execute(
                """
                update cursorDiskKV
                set value = json_set(value, '$.workspaceIdentifier', json(?))
                where key = ?
                """,
                [_identity_json(body_identity), f"composerData:{composer_id}"],
            )
            updated_bodies += body_cur.rowcount
    if updated != len(composer_ids):
        raise RuntimeError("Not all composer headers were restored.")
    if updated_bodies != int(workspace_identity.get("composer_data_count", 0)):
        raise RuntimeError("Not all composer data identities were restored.")
    return updated


def workspace_identity_status(
    db_path: Path,
    workspace_identity: dict[str, object],
    composer_ids: list[str],
    *,
    at_target: bool,
) -> dict[str, int | bool]:
    conn = connect_readonly(db_path)
    try:
        _load_ids_table(conn, composer_ids)
        header_rows = conn.execute(
            """
            select h.composerId,
                   json_extract(h.value, '$.workspaceIdentifier') as identity
            from composerHeaders h
            join restore_cursor_ids i on i.composerId = h.composerId
            """
        ).fetchall()
        body_rows = conn.execute(
            """
            select substr(d.key, length('composerData:') + 1) as composerId,
                   json_extract(d.value, '$.workspaceIdentifier') as identity
            from cursorDiskKV d
            join restore_cursor_ids i on d.key = 'composerData:' || i.composerId
            """
        ).fetchall()
    finally:
        conn.close()

    target_identity = workspace_identity.get("target")
    originals = workspace_identity.get("originals")
    if not isinstance(target_identity, dict) or not isinstance(originals, dict):
        return {
            "header_ok": False,
            "header_count": 0,
            "header_expected": len(composer_ids),
            "composer_data_ok": False,
            "composer_data_count": 0,
            "composer_data_expected": int(
                workspace_identity.get("composer_data_count", 0)
            ),
        }

    def expected_identity(composer_id: str, field: str) -> object:
        if at_target:
            return target_identity
        original = originals.get(composer_id)
        return original.get(field) if isinstance(original, dict) else None

    header_count = 0
    for row in header_rows:
        try:
            actual = json.loads(str(row["identity"]))
        except json.JSONDecodeError:
            continue
        if actual == expected_identity(str(row["composerId"]), "header"):
            header_count += 1

    body_count = 0
    for row in body_rows:
        try:
            actual = json.loads(str(row["identity"]))
        except json.JSONDecodeError:
            continue
        if actual == expected_identity(str(row["composerId"]), "composer_data"):
            body_count += 1

    body_expected = int(workspace_identity.get("composer_data_count", 0))
    return {
        "header_ok": header_count == len(composer_ids),
        "header_count": header_count,
        "header_expected": len(composer_ids),
        "composer_data_ok": body_count == body_expected,
        "composer_data_count": body_count,
        "composer_data_expected": body_expected,
    }


def restore_headers(
    db_path: Path,
    source: str,
    target: str,
    *,
    composer_ids: list[str] | None = None,
    workspace_identity: dict[str, object] | None = None,
) -> int:
    conn = connect_write(db_path)
    try:
        ensure_schema(conn)
        with conn:
            if workspace_identity is not None:
                if composer_ids is None:
                    raise RuntimeError(
                        "A workspace identity rewrite requires explicit composer IDs."
                    )
                cur_count = _apply_target_workspace_identity(
                    conn,
                    source,
                    target,
                    composer_ids,
                    workspace_identity,
                )
                return cur_count
            if composer_ids is None:
                cur = conn.execute(
                    "update composerHeaders set workspaceId = ? where workspaceId = ?",
                    [target, source],
                )
            else:
                _load_ids_table(conn, composer_ids)
                eligible = conn.execute(
                    """
                    select count(*) as n
                    from composerHeaders h
                    join restore_cursor_ids i on i.composerId = h.composerId
                    where h.workspaceId = ?
                    """,
                    [source],
                ).fetchone()["n"]
                if eligible != len(composer_ids):
                    raise RuntimeError(
                        "Chat headers changed after the dry run; refusing to apply."
                    )
                cur = conn.execute(
                    """
                    update composerHeaders
                    set workspaceId = ?
                    where workspaceId = ?
                      and composerId in (select composerId from restore_cursor_ids)
                    """,
                    [target, source],
                )
        return cur.rowcount
    finally:
        conn.close()


def _attach_workspace_state(
    conn: sqlite3.Connection, workspace_db_path: Path
) -> None:
    conn.execute("attach database ? as workspace_state", [str(workspace_db_path)])


def restore_headers_with_workspace_state(
    db_path: Path,
    source: str,
    target: str,
    composer_ids: list[str],
    workspace_state: dict[str, object],
    workspace_identity: dict[str, object] | None = None,
) -> int:
    conn = connect_write(db_path)
    try:
        ensure_schema(conn)
        _attach_workspace_state(conn, Path(str(workspace_state["database"])))
        with conn:
            _load_ids_table(conn, composer_ids)
            eligible = conn.execute(
                """
                select count(*) as n
                from composerHeaders h
                join restore_cursor_ids i on i.composerId = h.composerId
                where h.workspaceId = ?
                """,
                [source],
            ).fetchone()["n"]
            if eligible != len(composer_ids):
                raise RuntimeError(
                    "Chat headers changed after the dry run; refusing to apply."
                )
            state_row = conn.execute(
                "select value from workspace_state.ItemTable where key = ?",
                [str(workspace_state["key"])],
            ).fetchone()
            if state_row is None or state_row["value"] != workspace_state["previous_value"]:
                raise RuntimeError(
                    "Target workspace composer state changed after the snapshot; "
                    "refusing to apply."
                )
            if workspace_identity is None:
                cur = conn.execute(
                    """
                    update composerHeaders
                    set workspaceId = ?
                    where workspaceId = ?
                      and composerId in (select composerId from restore_cursor_ids)
                    """,
                    [target, source],
                )
                updated = cur.rowcount
            else:
                updated = _apply_target_workspace_identity(
                    conn,
                    source,
                    target,
                    composer_ids,
                    workspace_identity,
                )
            state_cur = conn.execute(
                """
                update workspace_state.ItemTable
                set value = ?
                where key = ? and value = ?
                """,
                [
                    str(workspace_state["next_value"]),
                    str(workspace_state["key"]),
                    str(workspace_state["previous_value"]),
                ],
            )
            if state_cur.rowcount != 1:
                raise RuntimeError("Target workspace composer state was not updated.")
        return updated
    finally:
        conn.close()


def undo_headers(
    db_path: Path,
    source: str,
    target: str,
    composer_ids: list[str],
    workspace_identity: dict[str, object] | None = None,
) -> int:
    conn = connect_write(db_path)
    try:
        ensure_schema(conn)
        with conn:
            if workspace_identity is not None:
                return _restore_source_workspace_identity(
                    conn,
                    source,
                    target,
                    composer_ids,
                    workspace_identity,
                )
            _load_ids_table(conn, composer_ids)
            eligible = conn.execute(
                """
                select count(*) as n
                from composerHeaders h
                join restore_cursor_ids i on i.composerId = h.composerId
                where h.workspaceId = ?
                """,
                [target],
            ).fetchone()["n"]
            if eligible != len(composer_ids):
                raise RuntimeError(
                    "Some restored chat headers no longer point at the recorded target; "
                    "refusing a partial undo."
                )
            cur = conn.execute(
                """
                update composerHeaders
                set workspaceId = ?
                where workspaceId = ?
                  and composerId in (select composerId from restore_cursor_ids)
                """,
                [source, target],
            )
        return cur.rowcount
    finally:
        conn.close()


def undo_headers_with_workspace_state(
    db_path: Path,
    source: str,
    target: str,
    composer_ids: list[str],
    workspace_state: dict[str, object],
    workspace_identity: dict[str, object] | None = None,
) -> int:
    conn = connect_write(db_path)
    try:
        ensure_schema(conn)
        _attach_workspace_state(conn, Path(str(workspace_state["database"])))
        with conn:
            _load_ids_table(conn, composer_ids)
            eligible = conn.execute(
                """
                select count(*) as n
                from composerHeaders h
                join restore_cursor_ids i on i.composerId = h.composerId
                where h.workspaceId = ?
                """,
                [target],
            ).fetchone()["n"]
            if eligible != len(composer_ids):
                raise RuntimeError(
                    "Some restored chat headers no longer point at the recorded target; "
                    "refusing a partial undo."
                )
            row = conn.execute(
                "select value from workspace_state.ItemTable where key = ?",
                [str(workspace_state["key"])],
            ).fetchone()
            if row is None:
                raise RuntimeError("Target workspace composer state is missing.")
            try:
                payload = json.loads(str(row["value"]))
            except json.JSONDecodeError as exc:
                raise RuntimeError(
                    "Target workspace composer state is not valid JSON."
                ) from exc
            if not isinstance(payload, dict):
                raise RuntimeError("Target workspace composer state is not a JSON object.")
            added = workspace_state.get("added", {})
            if not isinstance(added, dict):
                raise RuntimeError("Operation has invalid workspace attachment metadata.")
            for field, values in added.items():
                if not isinstance(field, str) or not isinstance(values, list):
                    raise RuntimeError("Operation has invalid workspace attachment metadata.")
                current = payload.get(field, [])
                if not isinstance(current, list):
                    raise RuntimeError(f"Target workspace composer state has invalid {field}.")
                payload[field] = [value for value in current if value not in values]
            if workspace_identity is None:
                cur = conn.execute(
                    """
                    update composerHeaders
                    set workspaceId = ?
                    where workspaceId = ?
                      and composerId in (select composerId from restore_cursor_ids)
                    """,
                    [source, target],
                )
                updated = cur.rowcount
            else:
                updated = _restore_source_workspace_identity(
                    conn,
                    source,
                    target,
                    composer_ids,
                    workspace_identity,
                )
            state_cur = conn.execute(
                "update workspace_state.ItemTable set value = ? where key = ?",
                [
                    json.dumps(payload, separators=(",", ":")),
                    str(workspace_state["key"]),
                ],
            )
            if state_cur.rowcount != 1:
                raise RuntimeError("Target workspace composer state was not updated.")
        return updated
    finally:
        conn.close()


def cursor_db_path_from_env() -> Path:
    return Path(os.environ.get("CURSOR_STATE_DB", DEFAULT_DB)).expanduser()
