"""Conservative XCAF assembly-component to DXF pairing.

Company STEP assemblies often contain useful part numbers in PRODUCT/XCAF
labels even when the outer STEP filename names only the assembly.  This module
extracts definition shapes without writing temporary STEP/STL files and pairs
them with one latest-revision DXF.  A filename match alone never promotes a
case: the normal projection/localization verifier still has to pass later.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional

from OCC.Core.Bnd import Bnd_Box
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.GProp import GProp_GProps
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopAbs import TopAbs_EDGE, TopAbs_FACE, TopAbs_SOLID
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.XCAFApp import XCAFApp_Application
from OCC.Core.XCAFDoc import XCAFDoc_DocumentTool

from auto_2d_drawing.step_reader import get_label_id, get_label_name
from auto_2d_drawing.tolerance.historical_manifest import PartIdentity, parse_part_identity


@dataclass
class NamedComponent:
    step_path: str
    label_entry: str
    name: str
    identity: PartIdentity
    shape: Any
    fingerprint: str


def _topology_count(shape, shape_type: int) -> int:
    count = 0
    explorer = TopExp_Explorer(shape, shape_type)
    while explorer.More():
        count += 1
        explorer.Next()
    return count


def shape_fingerprint(shape) -> str:
    """Return a placement-independent, deterministic coarse B-Rep signature."""
    bbox = Bnd_Box()
    brepbndlib.Add(shape, bbox)
    xmin, ymin, zmin, xmax, ymax, zmax = bbox.Get()
    extents = sorted((xmax - xmin, ymax - ymin, zmax - zmin))
    volume_props = GProp_GProps()
    surface_props = GProp_GProps()
    try:
        brepgprop.VolumeProperties(shape, volume_props)
        volume = float(volume_props.Mass())
    except Exception:
        volume = 0.0
    try:
        brepgprop.SurfaceProperties(shape, surface_props)
        surface = float(surface_props.Mass())
    except Exception:
        surface = 0.0
    payload = {
        "extents": [round(value, 4) for value in extents],
        "volume": round(volume, 4),
        "surface": round(surface, 4),
        "solids": _topology_count(shape, TopAbs_SOLID),
        "faces": _topology_count(shape, TopAbs_FACE),
        "edges": _topology_count(shape, TopAbs_EDGE),
    }
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha1(serialized.encode("utf-8")).hexdigest()[:16]


def extract_named_components(step_path: str) -> List[NamedComponent]:
    """Read unique leaf definition shapes carrying a company part number."""
    app = XCAFApp_Application.GetApplication()
    document = TDocStd_Document("MDTV-XCAF")
    app.NewDocument("MDTV-XCAF", document)
    reader = STEPCAFControl_Reader()
    reader.SetColorMode(False)
    reader.SetLayerMode(False)
    reader.SetNameMode(True)
    if reader.ReadFile(step_path) != IFSelect_RetDone or not reader.Transfer(document):
        return []

    shape_tool = XCAFDoc_DocumentTool.ShapeTool(document.Main())
    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    components: List[NamedComponent] = []
    visited_definitions = set()

    def visit(label) -> None:
        if shape_tool.IsReference(label):
            referred = TDF_Label()
            shape_tool.GetReferredShape(label, referred)
            visit(referred)
            return
        if shape_tool.IsAssembly(label):
            children = TDF_LabelSequence()
            shape_tool.GetComponents(label, children)
            for index in range(1, children.Length() + 1):
                visit(children.Value(index))
            return

        entry = get_label_id(label)
        if entry in visited_definitions:
            return
        visited_definitions.add(entry)
        name = get_label_name(label)
        identity = parse_part_identity(name)
        if identity is None:
            return
        shape = shape_tool.GetShape(label)
        if shape.IsNull():
            return
        components.append(NamedComponent(
            step_path=os.path.abspath(step_path),
            label_entry=entry,
            name=name,
            identity=identity,
            shape=shape,
            fingerprint=shape_fingerprint(shape),
        ))

    for index in range(1, free_shapes.Length() + 1):
        visit(free_shapes.Value(index))
    return components


def load_component_shape(step_path: str, label_entry: str):
    """Reload one component definition by its stable XCAF label entry."""
    for component in extract_named_components(step_path):
        if component.label_entry == label_entry:
            return component.shape
    return None


def _pair_record(component: NamedComponent, dxf_path: str, method: str, checks: List[str], equivalents) -> Dict[str, Any]:
    dxf_identity = parse_part_identity(dxf_path)
    return {
        "pair_id": f"{method}:{component.identity.part_number}:{dxf_identity.revision if dxf_identity else 'UNKNOWN'}",
        "step_path": component.step_path,
        "dxf_path": os.path.abspath(dxf_path),
        "pair_method": method,
        "eligible_for_auto_verification": True,
        "part_number": component.identity.part_number,
        "revision": dxf_identity.revision if dxf_identity else None,
        "component_name": component.name,
        "component_revision": component.identity.revision,
        "component_label_entry": component.label_entry,
        "component_fingerprint": component.fingerprint,
        "equivalent_step_sources": sorted({item.step_path for item in equivalents}, key=str.lower),
        "verification_checks": checks,
    }


def build_component_pair_manifest(step_paths: Iterable[str], dxf_paths: Iterable[str]) -> Dict[str, Any]:
    """Discover uniquely shaped XCAF components with one latest DXF target."""
    dxfs_by_part: Dict[str, List[str]] = defaultdict(list)
    for path in sorted({os.path.abspath(value) for value in dxf_paths}, key=str.lower):
        identity = parse_part_identity(path)
        if identity:
            dxfs_by_part[identity.part_number].append(path)

    components_by_part: Dict[str, List[NamedComponent]] = defaultdict(list)
    errors: List[Dict[str, str]] = []
    scanned_steps = 0
    for step_path in sorted({os.path.abspath(value) for value in step_paths}, key=str.lower):
        scanned_steps += 1
        try:
            for component in extract_named_components(step_path):
                components_by_part[component.identity.part_number].append(component)
        except Exception as exc:
            errors.append({"step_path": step_path, "error": str(exc)})

    verified_pairs: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for part_number in sorted(set(components_by_part) & set(dxfs_by_part)):
        components = components_by_part[part_number]
        dxfs = dxfs_by_part[part_number]
        if len(dxfs) != 1:
            candidates.append({
                "status": "AMBIGUOUS_COMPONENT_DXF",
                "part_number": part_number,
                "dxf_paths": dxfs,
            })
            continue
        dxf_path = dxfs[0]
        dxf_identity = parse_part_identity(dxf_path)
        exact_revision = [
            item for item in components
            if item.identity.revision is not None and item.identity.revision == dxf_identity.revision
        ]
        unversioned = [item for item in components if item.identity.revision is None]
        eligible = exact_revision or unversioned
        method = "XCAF_COMPONENT_EXACT_REVISION" if exact_revision else "XCAF_COMPONENT_UNVERSIONED_LATEST_DXF"
        if not eligible:
            candidates.append({
                "status": "COMPONENT_REVISION_MISMATCH",
                "part_number": part_number,
                "dxf_revision": dxf_identity.revision,
                "component_revisions": sorted({item.identity.revision for item in components if item.identity.revision}),
            })
            continue
        by_fingerprint: Dict[str, List[NamedComponent]] = defaultdict(list)
        for component in eligible:
            by_fingerprint[component.fingerprint].append(component)
        if len(by_fingerprint) != 1:
            candidates.append({
                "status": "AMBIGUOUS_COMPONENT_GEOMETRY",
                "part_number": part_number,
                "fingerprints": sorted(by_fingerprint),
                "step_paths": sorted({item.step_path for item in eligible}, key=str.lower),
            })
            continue
        equivalents = next(iter(by_fingerprint.values()))
        representative = sorted(equivalents, key=lambda item: (item.step_path.lower(), item.label_entry))[0]
        checks = [
            "xcaf_leaf_component_part_number",
            "unique_latest_dxf_for_component_part",
            "unique_component_geometry_fingerprint",
        ]
        if method == "XCAF_COMPONENT_EXACT_REVISION":
            checks.append("exact_component_revision_match")
        else:
            checks.append("component_revision_absent_requires_projection_v2")
        verified_pairs.append(_pair_record(representative, dxf_path, method, checks, equivalents))

    method_counts = Counter(item["pair_method"] for item in verified_pairs)
    status_counts = Counter(item["status"] for item in candidates)
    return {
        "schema_version": 1,
        "policy": {
            "latest_dxf_only": True,
            "cross_revision_component_pairing": False,
            "ambiguous_component_geometry_pairing": False,
            "projection_v2_still_required": True,
        },
        "verified_pairs": verified_pairs,
        "candidates": candidates,
        "errors": errors,
        "statistics": {
            "scanned_step_files": scanned_steps,
            "named_component_count": sum(len(values) for values in components_by_part.values()),
            "matched_pair_count": len(verified_pairs),
            "pair_methods": dict(method_counts),
            "candidate_statuses": dict(status_counts),
            "read_error_count": len(errors),
        },
    }
