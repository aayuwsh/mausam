import unittest
from pathlib import Path

try:
    import pdfplumber  # noqa: F401
except ImportError:
    pdfplumber = None

from backend.app.extract_crop_calendar import DEFAULT_PDFS, extract

PDF_DIR = Path.home() / "Downloads/Chrome P-2"


@unittest.skipIf(pdfplumber is None, "Install pdfplumber to run source PDF extraction tests.")
class CropPdfExtractionTests(unittest.TestCase):
    def test_three_provided_plan_tables_extract_with_pages_and_values(self):
        paths = [PDF_DIR / name for name in DEFAULT_PDFS]
        rows = extract(paths)
        self.assertEqual(len(rows), 31)
        self.assertEqual({row["district"] for row in rows}, {"East Champaran", "Muzaffarpur", "Patna"})
        self.assertTrue(all(row["source_page"] in {"6", "7"} for row in rows))
        self.assertIn(("May, week 1", "July, week 4"), {(r["sowing_start"], r["sowing_end"]) for r in rows if r["crop"] == "Pigeonpea"})
        self.assertTrue(all(not row["duration_days"] and not row["harvest_start"] for row in rows))


if __name__ == "__main__":
    unittest.main()
