import os
import tempfile
import unittest
from unittest.mock import patch

from OCC.Core.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCC.Core.gp import gp_Pnt

from auto_2d_drawing.tolerance.assembly_components import (
    NamedComponent,
    build_component_pair_manifest,
    shape_fingerprint,
)
from auto_2d_drawing.tolerance.historical_manifest import PartIdentity


def component(step_path, revision=None, fingerprint="same"):
    return NamedComponent(
        step_path=os.path.abspath(step_path),
        label_entry="0:1:1:2",
        name=f"SHAFT-2TEST5010H-{revision or 'UNVERSIONED'}",
        identity=PartIdentity(
            part_number="2TEST5010H",
            revision_kind=revision[0] if revision else None,
            revision_number=int(revision[1:]) if revision else None,
        ),
        shape=None,
        fingerprint=fingerprint,
    )


class AssemblyComponentTests(unittest.TestCase):
    def test_shape_fingerprint_ignores_absolute_placement(self):
        left = BRepPrimAPI_MakeBox(gp_Pnt(0, 0, 0), 10, 5, 2).Shape()
        right = BRepPrimAPI_MakeBox(gp_Pnt(100, -20, 30), 10, 5, 2).Shape()

        self.assertEqual(shape_fingerprint(left), shape_fingerprint(right))

    def test_exact_component_revision_is_preferred(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            step = os.path.join(temp_dir, "assembly.stp")
            dxf = os.path.join(temp_dir, "2TEST5010H-R02.dxf")
            values = [component(step, "R02"), component(step, None)]
            with patch(
                "auto_2d_drawing.tolerance.assembly_components.extract_named_components",
                return_value=values,
            ):
                manifest = build_component_pair_manifest([step], [dxf])

        self.assertEqual(len(manifest["verified_pairs"]), 1)
        self.assertEqual(
            manifest["verified_pairs"][0]["pair_method"],
            "XCAF_COMPONENT_EXACT_REVISION",
        )

    def test_unversioned_component_requires_latest_dxf_and_projection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            step = os.path.join(temp_dir, "assembly.stp")
            dxf = os.path.join(temp_dir, "2TEST5010H-R03.dxf")
            with patch(
                "auto_2d_drawing.tolerance.assembly_components.extract_named_components",
                return_value=[component(step, None)],
            ):
                manifest = build_component_pair_manifest([step], [dxf])

        pair = manifest["verified_pairs"][0]
        self.assertEqual(pair["pair_method"], "XCAF_COMPONENT_UNVERSIONED_LATEST_DXF")
        self.assertIn("component_revision_absent_requires_projection_v2", pair["verification_checks"])

    def test_conflicting_component_geometry_is_not_paired(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            steps = [os.path.join(temp_dir, "a.stp"), os.path.join(temp_dir, "b.stp")]
            dxf = os.path.join(temp_dir, "2TEST5010H-R03.dxf")

            def extracted(path):
                return [component(path, None, os.path.basename(path))]

            with patch(
                "auto_2d_drawing.tolerance.assembly_components.extract_named_components",
                side_effect=extracted,
            ):
                manifest = build_component_pair_manifest(steps, [dxf])

        self.assertEqual(manifest["verified_pairs"], [])
        self.assertEqual(manifest["candidates"][0]["status"], "AMBIGUOUS_COMPONENT_GEOMETRY")


if __name__ == "__main__":
    unittest.main()
