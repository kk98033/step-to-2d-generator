import os
import tempfile
import unittest

from auto_2d_drawing.tolerance.historical_manifest import (
    build_pair_manifest,
    parse_part_identity,
)


class HistoricalManifestTests(unittest.TestCase):
    def test_embedded_part_and_revision_are_parsed_from_assembly_name(self):
        identity = parse_part_identity("COVER_0FQWY1061H_R02.stp")
        self.assertIsNotNone(identity)
        self.assertEqual(identity.part_number, "0FQWY1061H")
        self.assertEqual(identity.revision, "R02")

    def test_unique_same_part_revision_pair_is_auto_verifiable(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            step = os.path.join(temp_dir, "COVER_0FQWY1061H_R02.stp")
            dxf = os.path.join(temp_dir, "0FQWY1061H-R02.dxf")
            manifest = build_pair_manifest([step], [dxf])

        self.assertEqual(len(manifest["verified_pairs"]), 1)
        pair = manifest["verified_pairs"][0]
        self.assertEqual(pair["pair_method"], "EMBEDDED_PART_EXACT_REVISION")
        self.assertEqual(pair["part_number"], "0FQWY1061H")

    def test_exact_filename_pair_has_priority_over_prefixed_model(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            exact_step = os.path.join(temp_dir, "1FQ6H3010H-R01.stp")
            assembly_step = os.path.join(temp_dir, "ROTOR_1FQ6H3010H_R01.stp")
            dxf = os.path.join(temp_dir, "1FQ6H3010H-R01.dxf")
            manifest = build_pair_manifest([exact_step, assembly_step], [dxf])

        self.assertEqual(len(manifest["verified_pairs"]), 1)
        pair = manifest["verified_pairs"][0]
        self.assertEqual(pair["pair_method"], "EXACT_FILENAME")
        self.assertEqual(pair["step_path"], os.path.abspath(exact_step))

    def test_cross_revision_pair_remains_review_candidate(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            step = os.path.join(temp_dir, "COVER_0FQWY1061H_R01.stp")
            dxf = os.path.join(temp_dir, "0FQWY1061H-R02.dxf")
            manifest = build_pair_manifest([step], [dxf])

        self.assertEqual(manifest["verified_pairs"], [])
        self.assertEqual(manifest["candidates"][0]["status"], "CROSS_REVISION_OR_MISSING_REVISION")

    def test_ambiguous_same_revision_pair_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            steps = [
                os.path.join(temp_dir, "COVER_0FQWY1061H_R02.stp"),
                os.path.join(temp_dir, "ASSY_0FQWY1061H_R02.stp"),
            ]
            dxf = os.path.join(temp_dir, "0FQWY1061H-R02.dxf")
            manifest = build_pair_manifest(steps, [dxf])

        self.assertEqual(manifest["verified_pairs"], [])
        self.assertEqual(manifest["candidates"][0]["status"], "AMBIGUOUS_PART_REVISION")


if __name__ == "__main__":
    unittest.main()
