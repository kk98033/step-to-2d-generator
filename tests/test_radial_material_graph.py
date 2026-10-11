from experimental_lab.tolerance_extraction_audit.common.scripts.radial_material_graph import (
    RadialMaterialGraphBuilder,
    RadialSurface,
)


def surface(radius, role, axial=(0.0, 1.0)):
    return RadialSurface(
        surface_id=f"surface_{radius}_{role}",
        radius=radius,
        diameter=2.0 * radius,
        axis=(0.0, 0.0, 1.0),
        axis_offset=(0.0, 0.0, 0.0),
        axial_span=axial,
        radial_inner_state="MATERIAL" if role == "MATERIAL_INSIDE_VOID_OUTSIDE" else "VOID",
        radial_outer_state="VOID" if role == "MATERIAL_INSIDE_VOID_OUTSIDE" else "MATERIAL",
        material_role=role,
        confidence=1.0,
        face_orientation_reversed=False,
    )


def test_material_annulus_is_classified_as_annular_hub():
    builder = RadialMaterialGraphBuilder()
    relations = builder._build_relations([
        surface(10.5, "VOID_INSIDE_MATERIAL_OUTSIDE"),
        surface(13.5, "MATERIAL_INSIDE_VOID_OUTSIDE"),
    ])
    assert len(relations) == 1
    assert relations[0]["feature_type"] == "annular_hub"
    assert relations[0]["inner_diameter"] == 21.0
    assert relations[0]["outer_diameter"] == 27.0


def test_void_between_cylinders_is_annular_interface_gap():
    builder = RadialMaterialGraphBuilder()
    relations = builder._build_relations([
        surface(10.5, "MATERIAL_INSIDE_VOID_OUTSIDE"),
        surface(13.5, "VOID_INSIDE_MATERIAL_OUTSIDE"),
    ])
    assert len(relations) == 1
    relation = relations[0]
    assert relation["feature_type"] == "annular_interface_gap"
    assert relation["inner_member_outer_diameter"] == 21.0
    assert relation["outer_member_inner_diameter"] == 27.0
    assert relation["radial_clearance"] == 3.0


def test_non_overlapping_axial_surfaces_are_not_paired():
    builder = RadialMaterialGraphBuilder()
    relations = builder._build_relations([
        surface(10.5, "MATERIAL_INSIDE_VOID_OUTSIDE", axial=(0.0, 1.0)),
        surface(13.5, "VOID_INSIDE_MATERIAL_OUTSIDE", axial=(2.0, 3.0)),
    ])
    assert relations == []


def test_relation_does_not_jump_over_an_intermediate_radial_boundary():
    builder = RadialMaterialGraphBuilder()
    relations = builder._build_relations([
        surface(10.0, "MATERIAL_INSIDE_VOID_OUTSIDE"),
        surface(11.0, "VOID_INSIDE_MATERIAL_OUTSIDE"),
        surface(13.0, "VOID_INSIDE_MATERIAL_OUTSIDE"),
    ])
    paired_diameters = {
        (
            relation.get("inner_member_outer_diameter"),
            relation.get("outer_member_inner_diameter"),
        )
        for relation in relations
        if relation["feature_type"] == "annular_interface_gap"
    }
    assert (20.0, 22.0) in paired_diameters
    assert (20.0, 26.0) not in paired_diameters
