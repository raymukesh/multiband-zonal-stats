import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("fast_zonal_utils", ROOT / "utils.py")
utils = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(utils)


class BandSelectionTests(unittest.TestCase):
    def test_all(self):
        self.assertEqual(utils.parse_band_selection("all", 4), [1, 2, 3, 4])

    def test_ranges_are_sorted_and_deduplicated(self):
        self.assertEqual(utils.parse_band_selection("5, 1-3, 2", 6), [1, 2, 3, 5])

    def test_out_of_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside"):
            utils.parse_band_selection("1,8", 7)

    def test_descending_range_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "ascending"):
            utils.parse_band_selection("4-2", 5)


class NamingTests(unittest.TestCase):
    def test_band_fallback_is_padded(self):
        self.assertEqual(utils.safe_band_name("", 7), "band_007")

    def test_column_names_are_safe_and_unique(self):
        self.assertEqual(
            utils.unique_names(["Mean value", "Mean value", "9 lives", ""]),
            ["mean_value", "mean_value_2", "_9_lives", "field"],
        )

    def test_header_names_keep_leading_digits_and_disambiguate(self):
        # Unlike column names, CSV headers keep a leading digit (2020, not _2020).
        self.assertEqual(
            utils.unique_header_names(["2020_NDVI", "Surface Temp", "Surface Temp"]),
            ["2020_ndvi", "surface_temp", "surface_temp_2"],
        )

    def test_csv_extension_is_added(self):
        self.assertTrue(utils.safe_output_path("result").endswith("result.csv"))


if __name__ == "__main__":
    unittest.main()

