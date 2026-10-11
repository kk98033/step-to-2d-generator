"""Build and audit radial semantics for selected diameter dimensions."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[4]
SCRIPT_ROOT = Path(__file__).resolve().parent
for item in (PROJECT_ROOT, SCRIPT_ROOT):
    if str(item) not in sys.path:
        sys.path.insert(0, str(item))

from auto_2d_drawing.tolerance.assembly_components import extract_leaf_components
from auto_2d_drawing.tolerance.ingest_historical_data import ExtractedDimension, _extract_dxf_worker
from radial_material_graph import RadialMaterialGraphBuilder


def run(step: Path, dxf: Path, component_name: str, handles, output: Path):
    component = next(
        item for item in extract_leaf_components(str(step))
        if item.name == component_name
    )
    builder = RadialMaterialGraphBuilder()
    graph = builder.build(component.shape)
    _source, payloads = _extract_dxf_worker(str(dxf))
    selected = [
        ExtractedDimension(**item) for item in payloads
        if not handles or str(item.get("entity_handle")) in handles
    ]
    dimensions = []
    for dimension in selected:
        if str(dimension.dimension_category).upper() != "DIAMETER":
            continue
        dimensions.append({
            "entity_handle": dimension.entity_handle,
            "nominal": dimension.nominal_value,
            "tolerance_config": dimension.tolerance_config,
            "radial_semantic": builder.classify_diameter(graph, dimension.nominal_value),
        })
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "production_updated": False,
        "component": component_name,
        "method": builder.METHOD,
        "graph": graph,
        "dimensions": dimensions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--step", required=True, type=Path)
    parser.add_argument("--dxf", required=True, type=Path)
    parser.add_argument("--component", required=True)
    parser.add_argument("--handles", nargs="*", default=[])
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = run(
        args.step.resolve(), args.dxf.resolve(), args.component,
        set(args.handles), args.output.resolve(),
    )
    print(json.dumps({"dimensions": report["dimensions"], "relation_count": len(report["graph"]["radial_relations"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
