from __future__ import annotations

import json
import shutil
import subprocess
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


MANIFEST_VERSION = 3
SUPPORTED_MANIFEST_VERSIONS = {1, 2, 3}
MANIFEST_NAME = "manifest.json"
GLOBAL_SNAPSHOT_NAMES = (
    "state.vscdb.bak",
    "state.vscdb-wal.bak",
    "state.vscdb-shm.bak",
)
WORKSPACE_SNAPSHOT_NAMES = (
    "workspace-state.vscdb.bak",
    "workspace-state.vscdb-wal.bak",
    "workspace-state.vscdb-shm.bak",
)
SNAPSHOT_NAMES = GLOBAL_SNAPSHOT_NAMES + WORKSPACE_SNAPSHOT_NAMES


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_backup_root(db_path: Path) -> Path:
    return db_path.with_name(f"{db_path.name}.restore-cursor-backups")


def _write_manifest(operation_dir: Path, manifest: dict[str, Any]) -> None:
    temp_path = operation_dir / f".{MANIFEST_NAME}.tmp"
    temp_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    temp_path.replace(operation_dir / MANIFEST_NAME)


def save_manifest(operation_dir: Path, manifest: dict[str, Any]) -> None:
    manifest["updated_at"] = utc_now()
    _write_manifest(operation_dir, manifest)


def _copy_snapshot(source: Path, destination: Path) -> str:
    clone = subprocess.run(
        ["cp", "-c", "-p", str(source), str(destination)],
        check=False,
        capture_output=True,
        text=True,
    )
    if clone.returncode == 0:
        if destination.stat().st_size != source.stat().st_size:
            destination.unlink(missing_ok=True)
            raise OSError("APFS clone size did not match its source.")
        return "apfs-clone"
    destination.unlink(missing_ok=True)
    free = shutil.disk_usage(destination.parent).free
    reserve = max(1024**3, source.stat().st_size // 20)
    if free < source.stat().st_size + reserve:
        raise OSError(
            "APFS cloning is unavailable and there is not enough free space for "
            "a full snapshot plus safety reserve."
        )
    shutil.copy2(source, destination)
    if destination.stat().st_size != source.stat().st_size:
        destination.unlink(missing_ok=True)
        raise OSError("Copied snapshot size did not match its source.")
    return "full-copy"


def create_restore_operation(
    db_path: Path,
    backup_root: Path,
    source: str,
    target: str,
    composer_ids: list[str],
    workspace_state: dict[str, object] | None = None,
    workspace_identity: dict[str, object] | None = None,
) -> tuple[Path, dict[str, Any]]:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    operation_id = f"{stamp}-{uuid.uuid4().hex[:8]}"
    operation_dir = backup_root / operation_id
    operation_dir.mkdir(parents=True, exist_ok=False)
    manifest: dict[str, Any] = {
        "schema_version": MANIFEST_VERSION,
        "operation_id": operation_id,
        "action": (
            "restore_headers_and_workspace_state"
            if workspace_state is not None
            else "restore_headers"
        ),
        "status": "creating_snapshot",
        "created_at": utc_now(),
        "updated_at": utc_now(),
        "database": str(db_path),
        "source_workspace_id": source,
        "target_workspace_id": target,
        "composer_ids": composer_ids,
        "header_count": len(composer_ids),
        "snapshot": {"present": False, "files": [], "methods": {}},
        "undo": {"status": "available", "applied_at": None},
    }
    if workspace_state is not None:
        manifest["workspace_state"] = workspace_state
    if workspace_identity is not None:
        manifest["workspace_identity"] = workspace_identity
    _write_manifest(operation_dir, manifest)

    try:
        snapshot_files: list[str] = []
        methods: dict[str, str] = {}
        sources = [
            (db_path, GLOBAL_SNAPSHOT_NAMES[0]),
            (Path(f"{db_path}-wal"), GLOBAL_SNAPSHOT_NAMES[1]),
            (Path(f"{db_path}-shm"), GLOBAL_SNAPSHOT_NAMES[2]),
        ]
        if workspace_state is not None:
            workspace_db = Path(str(workspace_state["database"]))
            sources.extend(
                [
                    (workspace_db, WORKSPACE_SNAPSHOT_NAMES[0]),
                    (Path(f"{workspace_db}-wal"), WORKSPACE_SNAPSHOT_NAMES[1]),
                    (Path(f"{workspace_db}-shm"), WORKSPACE_SNAPSHOT_NAMES[2]),
                ]
            )
        for source_path, snapshot_name in sources:
            if not source_path.exists():
                continue
            method = _copy_snapshot(source_path, operation_dir / snapshot_name)
            snapshot_files.append(snapshot_name)
            methods[snapshot_name] = method
        manifest["snapshot"] = {
            "present": True,
            "files": snapshot_files,
            "methods": methods,
        }
        manifest["status"] = "snapshot_ready"
        save_manifest(operation_dir, manifest)
        return operation_dir, manifest
    except Exception as exc:
        manifest["status"] = "snapshot_failed"
        manifest["error"] = str(exc)
        save_manifest(operation_dir, manifest)
        raise


def load_manifest(backup_root: Path, operation_id: str) -> tuple[Path, dict[str, Any]]:
    operation_dir = backup_root / operation_id
    if operation_dir.parent.resolve() != backup_root.resolve():
        raise ValueError("Invalid operation ID.")
    manifest_path = operation_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Operation not found: {operation_id}")
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("operation_id") != operation_id:
        raise ValueError("Manifest operation ID does not match its directory.")
    if manifest.get("schema_version") not in SUPPORTED_MANIFEST_VERSIONS:
        raise ValueError("Unsupported operation manifest version.")
    return operation_dir, manifest


def list_manifests(backup_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    if not backup_root.exists():
        return []
    records: list[tuple[Path, dict[str, Any]]] = []
    for manifest_path in backup_root.glob(f"*/{MANIFEST_NAME}"):
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        records.append((manifest_path.parent, manifest))
    return sorted(records, key=lambda item: str(item[1].get("created_at", "")), reverse=True)


def snapshot_disk_bytes(operation_dir: Path, manifest: dict[str, Any]) -> int:
    total = 0
    for name in manifest.get("snapshot", {}).get("files", []):
        if name not in SNAPSHOT_NAMES:
            continue
        path = operation_dir / name
        if path.exists():
            total += path.stat().st_blocks * 512
    return total


def snapshot_state(operation_dir: Path, manifest: dict[str, Any]) -> str:
    snapshot = manifest.get("snapshot", {})
    if not snapshot.get("present"):
        return "deleted"
    names = [name for name in snapshot.get("files", []) if name in SNAPSHOT_NAMES]
    if names and all((operation_dir / name).is_file() for name in names):
        return "present"
    return "missing"


def delete_snapshot(operation_dir: Path, manifest: dict[str, Any]) -> int:
    removed = 0
    for name in manifest.get("snapshot", {}).get("files", []):
        if name not in SNAPSHOT_NAMES:
            raise ValueError(f"Refusing unexpected snapshot filename: {name}")
        path = operation_dir / name
        if path.exists():
            removed += path.stat().st_blocks * 512
            path.unlink()
    manifest["snapshot"]["present"] = False
    manifest["snapshot"]["deleted_at"] = utc_now()
    save_manifest(operation_dir, manifest)
    return removed
