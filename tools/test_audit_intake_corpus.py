import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit_intake_corpus.py"


class AuditHarnessTests(unittest.TestCase):
    def test_missing_live_index_fails_closed_instead_of_using_synthetic(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                [sys.executable, str(TOOL), tmp], cwd=ROOT,
                capture_output=True, text=True,
            )
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
            self.assertIn("live index is missing", result.stderr)

    def test_synthetic_report_has_at_least_fifty_records_and_required_aggregates(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--synthetic", "--count", "60"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        report = json.loads(result.stdout)
        self.assertEqual(report["source_mode"], "synthetic")
        self.assertEqual(report["records"], 60)
        for key in (
            "status_counts",
            "reason_counts",
            "provider_counts",
            "category_counts",
            "intent_counts",
            "fact_presence",
            "validation_result_counts",
            "trip_match_result_counts",
            "persistence_result_counts",
            "failure_stage_counts",
            "diagnosis_counts",
        ):
            self.assertIn(key, report)
        self.assertIn("processed_but_not_visible", report["diagnosis_counts"])
        self.assertIn("unknown_facts", report["diagnosis_counts"])
        self.assertEqual(report["release_gate"]["false_processed_count"], 0)
        self.assertGreater(report["release_gate"]["processed_but_not_visible_count"], 0)

    def test_release_gate_is_machine_readable_and_fails_on_visibility_violation(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--synthetic", "--count", "10", "--release-gate"],
            cwd=ROOT, check=False, capture_output=True, text=True,
        )
        self.assertNotEqual(result.returncode, 0)
        gate = json.loads(result.stdout)
        self.assertFalse(gate["passed"])
        self.assertEqual(gate["source_mode"], "synthetic")
        self.assertEqual(gate["false_processed_count"], 0)
        self.assertGreater(gate["processed_but_not_visible_count"], 0)
        self.assertNotIn("synthetic-trip-", result.stdout)

    def test_release_gate_passes_for_clean_supplied_corpus(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            intake = Path(tmp)
            (intake / "index.json").write_text(json.dumps([{
                "id": "private-record", "status": "processed", "trip_id": "private-trip",
                "processing_stage": "persisted", "ingested_type": "hotel",
                "booking_facts": {"facts": {"hotel": "Private Hotel", "city": "Private City",
                                               "check_in": "2026-01-01", "check_out": "2026-01-02"}},
                "extraction_telemetry": [{"persistence_outcome": "created"}],
            }]), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), str(intake), "--release-gate"],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            gate = json.loads(result.stdout)
            self.assertTrue(gate["passed"])
            self.assertEqual(gate["false_processed_count"], 0)
            self.assertEqual(gate["processed_but_not_visible_count"], 0)
            self.assertNotIn("Private Hotel", result.stdout)
            self.assertNotIn("private-record", result.stdout)

    def test_latest_only_can_verify_booking_is_in_the_trip_without_emitting_ids(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intake = root / "email"
            intake.mkdir()
            (intake / "index.json").write_text(json.dumps([{
                "id": "private-mail", "status": "processed", "intent": "confirmed", "ingested_type": "hotel",
                "trip_id": "private-trip", "document": "private-document.txt",
                "extraction_telemetry": [{"persistence_outcome": "created"}],
            }]), encoding="utf-8")
            trips_path = root / "trips.json"
            trips_path.write_text(json.dumps([{
                "id": "private-trip", "hotels": [{"source_email_id": "private-mail", "document": "private-document.txt", "name": "Private Hotel"}]
            }]), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), str(intake), "--latest-only", "--trip-data-file", str(trips_path)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            summary = json.loads(result.stdout)
            self.assertTrue(summary["booking_visible"])
            self.assertEqual(summary["trip_match"], "matched_visible")
            self.assertTrue(summary["trip_id_resolves"])
            self.assertNotIn("private-mail", result.stdout)
            self.assertNotIn("Private Hotel", result.stdout)

    def test_latest_only_cli_emits_safe_summary_without_corpus_details(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--synthetic", "--count", "5", "--latest-only"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        )
        summary = json.loads(result.stdout)
        self.assertIn("status", summary)
        self.assertNotIn("provider_counts", summary)
        self.assertNotIn("SYNTHETIC-CONFIRMATION", result.stdout)

    def test_report_never_contains_fixture_identifiers_or_raw_fact_values(self):
        result = subprocess.run(
            [sys.executable, str(TOOL), "--synthetic", "--count", "60"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertNotIn("SYNTHETIC-CONFIRMATION", result.stdout)
        self.assertNotIn("Passenger Synthetic", result.stdout)
        self.assertNotIn("example.test", result.stdout)

    def test_field_evidence_uses_nested_booking_facts_and_validation_field(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            intake = Path(tmp)
            (intake / "index.json").write_text(json.dumps({"records": [{
                "id": "private-record",
                "status": "needs_review",
                "processing_stage": "validating",
                "ingested_type": "hotel",
                "booking_facts": {"type": "hotel", "facts": {
                    "hotel": "Private Hotel", "city": "Private City", "check_in": "2026-01-01"
                }},
                "extraction_telemetry": [{
                    "normalized_type": "hotel",
                    "validation_error_code": "validation_failed",
                    "validation_error_field": "check_out",
                    "persistence_outcome": "not_persisted",
                }],
            }] }), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), str(intake)], cwd=ROOT,
                check=True, capture_output=True, text=True,
            )
            report = json.loads(result.stdout)
            self.assertEqual(report["fact_presence"]["hotel.hotel"], {"present": 1})
            self.assertEqual(report["fact_presence"]["hotel.check_out"], {"missing": 1})
            self.assertEqual(report["failure_stage_counts"], {"validating": 1})
            self.assertEqual(report["field_evidence_counts"]["hotel.check_out.validation_error"], {"present": 1})
            self.assertNotIn("Private Hotel", result.stdout)
            self.assertNotIn("Private City", result.stdout)

    def test_tripcom_match_diagnostic_compares_old_and_seven_day_policies_without_values(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            intake = root / "email"
            intake.mkdir()
            (intake / "index.json").write_text(json.dumps([{
                "id": "private-mail", "from": "noreply@mailer.trip.com", "subject": "Trip.com booking confirmation",
                "status": "processed", "intent": "confirmed", "ingested_type": "hotel",
                "trip_id": "generated-trip", "document": "private-document.txt",
                "extraction_telemetry": [{"persistence_outcome": "created"}],
            }]), encoding="utf-8")
            trips_path = root / "trips.json"
            trips_path.write_text(json.dumps([
                {"id": "generated-trip", "start": "2026-12-10", "end": "2026-12-13", "hotels": [
                    {"source_email_id": "private-mail", "document": "private-document.txt", "start": "2026-12-10", "end": "2026-12-13", "country": "Italy"}
                ]},
                {"id": "existing-trip", "start": "2026-12-11", "end": "2026-12-12", "destinations": [{"country": "Georgia"}], "hotels": [
                    {"name": "Private Hotel", "start": "2026-12-11", "end": "2026-12-12", "country": "Georgia"}
                ]},
            ]), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(TOOL), str(intake), "--latest-only", "--trip-data-file", str(trips_path)],
                cwd=ROOT, check=True, capture_output=True, text=True,
            )
            summary = json.loads(result.stdout)["latest_tripcom_booking_summary"]
            self.assertEqual(summary["category"], "hotel")
            self.assertEqual(summary["status"], "processed")
            self.assertTrue(summary["booking_visible"])
            self.assertEqual(summary["prior_policy_candidate_count"], 0)
            self.assertEqual(summary["seven_day_policy_candidate_count"], 1)
            self.assertNotIn("private-mail", result.stdout)
            self.assertNotIn("Private Hotel", result.stdout)
            self.assertNotIn("Italy", result.stdout)
            self.assertNotIn("Georgia", result.stdout)

    def test_latest_cancellation_record_exposes_only_safe_intent_conflict_and_match_stage(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            intake = Path(tmp)
            (intake / "index.json").write_text(json.dumps([{
                "id": "private-record", "status": "needs_review", "received_at": "2026-10-15T13:00:00Z",
                "subject": "Private hotel booking subject", "from": "private@example.test", "intent": "cancelled",
                "ingested_type": "hotel", "processing_stage": "unknown",
                "extraction_telemetry": [{"validation_error_code": "no_unique_match"}],
            }]), encoding="utf-8")
            folder = intake / "private-record"
            folder.mkdir()
            (folder / "body.txt").write_text("Your reservation is confirmed. Free cancellation until 2026-10-20.", encoding="utf-8")
            result = subprocess.run([sys.executable, str(TOOL), str(intake)], cwd=ROOT, check=True, capture_output=True, text=True)
            latest = json.loads(result.stdout)["latest_record_summary"]
            self.assertEqual(latest["intent"], "cancelled")
            self.assertEqual(latest["source_intent"], "confirmed")
            self.assertFalse(latest["source_intent_matches_record"])
            self.assertEqual(latest["stage"], "matching")
            self.assertNotIn("Private hotel booking subject", result.stdout)
            self.assertNotIn("Your reservation is confirmed", result.stdout)

    def test_live_audit_reports_only_safe_latest_record_diagnosis(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            intake = Path(tmp)
            (intake / "index.json").write_text(json.dumps([
                {"id": "private-old", "status": "processed", "received_at": "2026-10-10T00:00:00Z", "ingested_type": "hotel"},
                {"id": "private-new", "status": "needs_review", "received_at": "2026-10-15T13:00:00Z", "subject": "Private subject", "from": "private@example.test", "ingested_type": "hotel", "processing_stage": "validating", "review_reason": "private raw reason", "attachments": [], "extraction_telemetry": [{"validation_error_code": "validation_failed"}]},
            ]), encoding="utf-8")
            result = subprocess.run([sys.executable, str(TOOL), str(intake)], cwd=ROOT, check=True, capture_output=True, text=True)
            report = json.loads(result.stdout)
            latest = report["latest_record_summary"]
            self.assertEqual(latest["status"], "needs_review")
            self.assertEqual(latest["category"], "hotel")
            self.assertEqual(latest["stage"], "validating")
            self.assertEqual(latest["failure_code"], "validation_failed")
            self.assertNotIn("private-new", result.stdout)
            self.assertNotIn("Private subject", result.stdout)
            self.assertNotIn("private@example.test", result.stdout)
            self.assertNotIn("private raw reason", result.stdout)

    def test_live_index_is_read_without_emitting_record_detail(self):
        with self.subTest("fixture index"):
            import tempfile
            with tempfile.TemporaryDirectory() as tmp:
                intake = Path(tmp)
                (intake / "index.json").write_text(json.dumps([{
                    "id": "private-record",
                    "subject": "Hotel reservation confirmation",
                    "from": "not-emitted-address",
                    "status": "processed",
                    "processing_stage": "persisted",
                    "trip_id": "private-trip",
                    "ingested_type": "hotel",
                    "extraction_telemetry": [{
                        "normalized_type": "hotel",
                        "validation_error_code": "",
                        "persistence_outcome": "created",
                    }],
                }]), encoding="utf-8")
                result = subprocess.run(
                    [sys.executable, str(TOOL), str(intake)],
                    cwd=ROOT,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                report = json.loads(result.stdout)
                summary = report["latest_record_summary"]
                self.assertEqual(report["records"], 1)
                self.assertEqual(summary["trip_match"], "matched")
                self.assertEqual(summary["persistence"], "created")
                self.assertTrue(summary["trip_id_present"])
                self.assertEqual(summary["failure_code"], "none")
                self.assertNotIn("private-record", result.stdout)
                self.assertNotIn("not-emitted-address", result.stdout)


if __name__ == "__main__":
    unittest.main()
