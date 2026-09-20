import tempfile
import unittest
from pathlib import Path

import build


class BuildTests(unittest.TestCase):
    def test_build_strips_internal_sections_and_keeps_version_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            input_dir = Path(directory)
            (input_dir / "spec-001-example.md").write_text(
                """---
stakeholder: true
---
# Area {#area}
## Feature {#area.feature}
See [the policy](https://example.com).
### Public details
- Visible
### Internal details
<!-- internal -->
Secret
""",
                encoding="utf-8",
            )

            first, warnings = build.build_document(input_dir, "Project", "Draft")
            second, _ = build.build_document(input_dir, "Project", "Draft")

            feature = first["nodes"][0]["children"][0]
            self.assertEqual([], warnings)
            self.assertEqual("See the policy.", feature["summary"])
            self.assertIn("Visible", feature["details_html"])
            self.assertNotIn("Secret", feature["details_html"])
            self.assertEqual(first["version"], second["version"])

    def test_synthetic_title_removes_spec_number_prefix(self):
        self.assertEqual("Case Routing", build.title_from_filename(Path("spec-001-case-routing.md")))


if __name__ == "__main__":
    unittest.main()

