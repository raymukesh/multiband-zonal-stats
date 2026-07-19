import importlib.util
import sys
import types
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = types.ModuleType("fast_zonal_testpkg")
PACKAGE.__path__ = [str(ROOT)]
sys.modules[PACKAGE.__name__] = PACKAGE
for module_name in ("utils", "engine"):
    spec = importlib.util.spec_from_file_location(
        f"{PACKAGE.__name__}.{module_name}", ROOT / f"{module_name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
engine = sys.modules[f"{PACKAGE.__name__}.engine"]


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

    def test_projected_pixel_area_honours_linear_units(self):
        class FakeSrs:
            def IsProjected(self):
                return True

            def GetLinearUnits(self):
                return 0.3048  # feet to metres

        area = engine._pixel_area_m2(FakeSrs(), (0, 10, 0, 0, 0, -20))
        self.assertAlmostEqual(area, 200 * 0.3048**2)


if __name__ == "__main__":
    unittest.main()
