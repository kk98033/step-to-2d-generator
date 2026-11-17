"""Contracts that DXF audit alone cannot establish."""
import sys
import tempfile
import unittest
from pathlib import Path

import ezdxf
from ezdxf.math import Matrix44

sys.path.insert(0,str(Path(__file__).resolve().parent))
import renderer
sys.path.insert(0,str(renderer.SCRIPTS))
from engineering_dimension_renderer import diameter_arrow_audit


class NativeDimensionContracts(unittest.TestCase):
    def test_scale_five_has_native_decimal_measurements(self):
        for value in (0.9,2.0995881162,16.300411883798,22.1):
            with self.subTest(value=value):
                doc=ezdxf.new(setup=True)
                dim=doc.modelspace().add_aligned_dim((0,0),(value*5,0),distance=12,
                    text=f"{value:.2f}",dimstyle="EZDXF")
                dim.render()
                entity=dim.dimension
                entity.transform(Matrix44.scale(0.2))
                renderer._native_text(entity,5)
                self.assertAlmostEqual(entity.get_measurement(),value,places=8)
                self.assertEqual(entity.dxf.text,"<>")
                texts=[e.plain_text() for e in entity.virtual_entities() if e.dxftype()=="MTEXT"]
                self.assertEqual(texts,[f"{value:.2f}"])
                self.assertFalse(doc.audit().errors)

    def test_tolerance_is_native_cad_data(self):
        doc=ezdxf.new(setup=True)
        dim=doc.modelspace().add_aligned_dim((0,0),(110.5,0),distance=12,
                    text="22.10(+0.000/-0.100)",dimstyle="EZDXF")
        dim.render()
        dim.dimension.transform(Matrix44.scale(0.2))
        renderer._native_text(dim.dimension,5)
        native=dim.dimension.override()
        self.assertEqual(native.get("dimtol"),1)
        self.assertEqual(native.get("dimtp"),0)
        self.assertEqual(native.get("dimtm"),0.1)

    def test_misleading_text_is_rejected(self):
        doc=ezdxf.new(setup=True)
        dim=doc.modelspace().add_aligned_dim((0,0),(81.5,0),distance=12,text="16.30")
        dim.render()
        with self.assertRaisesRegex(ValueError,"disagrees"):
            renderer._native_text(dim.dimension,1)

    def test_diameter_has_one_symbol_and_two_inward_arrows(self):
        doc=ezdxf.new(setup=True)
        dim=doc.modelspace().add_diameter_dim(center=(0,0),radius=80,angle=145,
                    text="%%c32.00",dimstyle="EZDXF")
        dim.render()
        dim.dimension.transform(Matrix44.scale(0.2))
        renderer._native_text(dim.dimension,5)
        self.assertAlmostEqual(dim.dimension.get_measurement(),32)
        self.assertTrue(diameter_arrow_audit(dim.dimension)["arrowheads_point_inward"])
        texts=[e.plain_text() for e in dim.dimension.virtual_entities() if e.dxftype()=="MTEXT"]
        self.assertEqual(sum(t.count("Ø") for t in texts),1)

    def test_leader_label_centre_survives_scale_conversion(self):
        doc=ezdxf.new(setup=True)
        dim=doc.modelspace().add_aligned_dim((0,0),(150,0),distance=12,
                    text="30.00",dimstyle="EZDXF")
        dim.set_location((180,50),leader=True,relative=False)
        dim.render()
        dim.dimension.transform(Matrix44.scale(0.2))
        renderer._native_text(dim.dimension,5)
        text=next(e for e in dim.dimension.virtual_entities() if e.dxftype()=="MTEXT")
        self.assertAlmostEqual(text.dxf.insert.x,36)
        self.assertAlmostEqual(text.dxf.insert.y,10)

    def test_flat_2d_duplicate_labels_are_detected(self):
        doc=ezdxf.new(setup=True)
        for _ in range(2):
            doc.modelspace().add_aligned_dim((0,0),(16.3,0),distance=5,text="16.30").render()
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/"duplicate.dxf"
            doc.saveas(path)
            self.assertEqual(len(renderer.audit(path)["text_collision_pairs"]),1)


if __name__=="__main__": unittest.main()
