import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from backup_deploy_state import BackupError, backup_persistent_state


class DeployStateBackupTests(unittest.TestCase):
    def test_backup_copies_only_mutable_json_state_with_private_permissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            email = root / "email"
            backups = root / "backups"
            data.mkdir()
            email.mkdir()
            (data / "trip-data.json").write_text('{"synthetic":"trip-state"}', encoding="utf-8")
            (data / "trips.json").write_text('{"synthetic":"legacy-state"}', encoding="utf-8")
            (email / "index.json").write_text('[{"synthetic":"intake-state"}]', encoding="utf-8")

            result = backup_persistent_state(data, email, backups, run_id="123", commit_sha="abc1234")

            target = Path(result["directory"])
            self.assertEqual(result["files"], 3)
            self.assertEqual(result["bytes"], sum((data / name).stat().st_size for name in ("trip-data.json", "trips.json")) + (email / "index.json").stat().st_size)
            self.assertEqual((target / "trip-data.json").read_text(encoding="utf-8"), '{"synthetic":"trip-state"}')
            self.assertEqual((target / "trips.json").read_text(encoding="utf-8"), '{"synthetic":"legacy-state"}')
            self.assertEqual((target / "index.json").read_text(encoding="utf-8"), '[{"synthetic":"intake-state"}]')
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o700)
            for name in ("trip-data.json", "trips.json", "index.json"):
                self.assertEqual(stat.S_IMODE((target / name).stat().st_mode), 0o600)

    def test_backup_fails_closed_when_no_mutable_state_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(BackupError):
                backup_persistent_state(root / "data", root / "email", root / "backups", run_id="123", commit_sha="abc1234")

    def test_backup_does_not_remove_existing_backup_when_run_id_is_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            email = root / "email"
            backups = root / "backups"
            data.mkdir()
            email.mkdir()
            (data / "trip-data.json").write_text("synthetic", encoding="utf-8")
            existing = backups / "123-abc1234"
            existing.mkdir(parents=True)
            marker = existing / "preserve.txt"
            marker.write_text("SYNTHETIC EXISTING BACKUP", encoding="utf-8")

            with self.assertRaises(BackupError):
                backup_persistent_state(data, email, backups, run_id="123", commit_sha="abc1234")

            self.assertEqual(marker.read_text(encoding="utf-8"), "SYNTHETIC EXISTING BACKUP")

    def test_backup_fails_before_copy_when_free_space_is_insufficient(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "data"
            email = root / "email"
            backups = root / "backups"
            data.mkdir()
            email.mkdir()
            (data / "trip-data.json").write_text("synthetic", encoding="utf-8")
            with patch("backup_deploy_state.shutil.disk_usage", return_value=type("Usage", (), {"free": 1})()):
                with self.assertRaises(BackupError):
                    backup_persistent_state(data, email, backups, run_id="123", commit_sha="abc1234")
            self.assertFalse((backups / "123-abc1234").exists())


    def test_deploy_workflow_backs_up_mutable_state_before_container_replacement(self):
        workflow = (ROOT / ".github" / "workflows" / "deploy-ugreen.yml").read_text(encoding="utf-8")
        backup_step = workflow.index("name: Back up mutable JSON state before startup recovery")
        build_step = workflow.index("name: Build release image")
        replacement = workflow.index('docker rm -f "$CONTAINER_NAME"')
        self.assertLess(backup_step, build_step)
        self.assertLess(backup_step, replacement)
        self.assertIn("tools/backup_deploy_state.py", workflow)
        self.assertIn("BACKUP_DIR: /home/Kobic101/tripy-backups", workflow)
        self.assertIn("EMAIL_DIR: /home/Kobic101/tripy-email", workflow)


if __name__ == "__main__":
    unittest.main()
