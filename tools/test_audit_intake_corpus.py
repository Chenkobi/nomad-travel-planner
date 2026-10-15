import json
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "tools" / "audit_intake_corpus.py"


class AuditHarnessTests(unittest.TestCase):
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
                self.assertNotIn("private-record", result.stdout)
                self.assertNotIn("not-emitted-address", result.stdout)
                self.assertEqual(json.loads(result.stdout)["records"], 1)


if __name__ == "__main__":
    unittest.main()
