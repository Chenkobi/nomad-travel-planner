#!/usr/bin/env python3
"""Back up only mutable TRIPY JSON state before a production container restart."""
from __future__ import annotations

import argparse
import filecmp
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any


class BackupError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def backup_persistent_state(
    data_dir: str | Path,
    email_dir: str | Path,
    backup_root: str | Path,
    *,
    run_id: str,
    commit_sha: str,
    reserve_bytes: int = 1024 * 1024,
) -> dict[str, Any]:
    if not re.fullmatch(r"[0-9]+", str(run_id)) or not re.fullmatch(r"[0-9a-fA-F]{7,64}", str(commit_sha)):
        raise BackupError("invalid_backup_id")

    data_dir = Path(data_dir)
    email_dir = Path(email_dir)
    sources = [data_dir / "trip-data.json", data_dir / "trips.json", email_dir / "index.json"]
    for source in sources:
        if source.is_symlink():
            raise BackupError("unexpected_symlink")
    existing = [source for source in sources if source.is_file()]
    if not existing:
        raise BackupError("no_mutable_state")

    total_bytes = sum(source.stat().st_size for source in existing)
    root = Path(backup_root)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    if shutil.disk_usage(root).free < total_bytes + max(0, int(reserve_bytes)):
        raise BackupError("insufficient_space")

    target = root / f"{run_id}-{str(commit_sha)[:12]}"
    if target.exists():
        raise BackupError("backup_exists")
    created = False
    try:
        target.mkdir(mode=0o700)
        created = True
        target.chmod(0o700)
        for source in existing:
            destination = target / source.name
            temporary = target / (source.name + ".partial")
            with source.open("rb") as source_file, temporary.open("xb") as target_file:
                shutil.copyfileobj(source_file, target_file)
                target_file.flush()
                os.fsync(target_file.fileno())
            temporary.chmod(0o600)
            os.replace(temporary, destination)
            if source.stat().st_size != destination.stat().st_size or not filecmp.cmp(source, destination, shallow=False):
                raise BackupError("verification_failed")
        return {"directory": str(target), "files": len(existing), "bytes": total_bytes}
    except Exception:
        if created:
            shutil.rmtree(target, ignore_errors=True)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--email-dir", required=True)
    parser.add_argument("--backup-dir", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--commit-sha", required=True)
    args = parser.parse_args()
    try:
        result = backup_persistent_state(
            args.data_dir,
            args.email_dir,
            args.backup_dir,
            run_id=args.run_id,
            commit_sha=args.commit_sha,
        )
    except BackupError as exc:
        print(f"deploy_state_backup=failed reason={exc.code}", file=sys.stderr)
        return 1
    except OSError:
        print("deploy_state_backup=failed reason=io_error", file=sys.stderr)
        return 1
    print(f"deploy_state_backup=verified files={result['files']} bytes={result['bytes']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
