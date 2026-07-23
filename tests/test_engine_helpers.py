import sys
import unittest
from pathlib import Path

# Works whether this file is run directly or discovered as tests.<module>.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from plugin_modules import load  # noqa: E402

_, engine = load()


class OverlapPassTests(unittest.TestCase):
    def zone(self, number, bounds):
        return engine.Zone(number, number, str(number), b"", bounds)

    def test_overlapping_boxes_are_separated(self):
        zones = [self.zone(1, (0, 0, 2, 2)), self.zone(2, (1, 1, 3, 3))]
        passes = engine.non_overlapping_passes(zones)
        self.assertEqual(len(passes), 2)

    def test_non_overlapping_boxes_share_pass(self):
        zones = [self.zone(1, (0, 0, 1, 1)), self.zone(2, (2, 2, 3, 3))]
        passes = engine.non_overlapping_passes(zones)
        self.assertEqual(len(passes), 1)

    def test_touching_edges_are_conservatively_separated(self):
        zones = [self.zone(1, (0, 0, 1, 1)), self.zone(2, (1, 0, 2, 1))]
        passes = engine.non_overlapping_passes(zones)
        self.assertEqual(len(passes), 2)

    def test_candidate_tiles_exclude_distant_tiles(self):
        zones = [self.zone(1, (10, 70, 29, 89))]
        # North-up 100x100 raster, 1 map unit pixels, 20px tiles.
        candidates = engine._candidate_tiles(zones, (0, 1, 0, 100, 0, -1), 100, 100, 20)
        self.assertEqual(set(candidates), {(0, 0), (1, 0), (0, 1), (1, 1)})


if __name__ == "__main__":
    unittest.main()
