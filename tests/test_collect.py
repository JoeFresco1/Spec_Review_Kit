import csv
import tempfile
import unittest
from pathlib import Path

import collect


class CollectTests(unittest.TestCase):
    def setUp(self):
        self.data = {
            "schema": "spec-review-data-1",
            "project": "Project",
            "version": "abc123",
            "nodes": [],
        }

    def test_validation_reports_mismatches_and_unknown_decision(self):
        payload = {
            "_file": "feedback.json",
            "project": "Other",
            "reviewed_version": "old",
            "feedback": {"feature.one": {"decision": "maybe", "comment": ""}},
        }
        warnings = collect.validate_feedback(self.data, [payload])
        self.assertEqual(3, len(warnings))
        self.assertTrue(any("project" in warning for warning in warnings))
        self.assertTrue(any("reviewed version" in warning for warning in warnings))
        self.assertTrue(any("unrecognized decision" in warning for warning in warnings))

    def test_matrix_neutralizes_formula_cells(self):
        rows = [{"id": "feature.one", "area": "Area", "title": "Feature", "path": "Area / Feature"}]
        payload = {
            "_reviewer": "Reviewer",
            "feedback": {"feature.one": {"decision": "need", "comment": "=HYPERLINK(\"bad\")"}},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "matrix.csv"
            collect.write_matrix(path, rows, [payload], ["Reviewer"])
            with path.open(encoding="utf-8-sig", newline="") as handle:
                output = list(csv.reader(handle))
        self.assertEqual("'=HYPERLINK(\"bad\")", output[1][4])

    def test_digest_surfaces_unknown_status(self):
        rows = [{"id": "feature.one", "area": "Area", "title": "Feature", "path": "Area / Feature"}]
        payload = {
            "_reviewer": "Reviewer",
            "feedback": {"feature.one": {"decision": "maybe", "comment": "Needs a call"}},
            "reviewed_version": "abc123",
        }
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "digest.md"
            collect.write_digest(path, self.data, rows, [payload], ["Reviewer"])
            digest = path.read_text(encoding="utf-8")
        self.assertIn("## Unrecognized statuses", digest)
        self.assertIn("`maybe`", digest)


if __name__ == "__main__":
    unittest.main()
