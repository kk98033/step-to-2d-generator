"""Single, production feature-extraction entry point.

The web feature panel, smart annotation rules, historical drawing ingestion,
and engineer-confirmed cases must all use the records returned here.  Keeping
this small wrapper around the existing production extractor prevents each
consumer from inventing a second feature identity scheme.
"""

import copy
from collections import Counter
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from auto_2d_drawing.feature_extractor import FeatureExtractor
from auto_2d_drawing.feature_layer import build_feature_records
from auto_2d_drawing.part_classifier import PartClassifier


CANONICAL_FEATURE_SCHEMA = "web_feature_records_v1"


@dataclass
class CanonicalFeatureSet:
    feature_extractor: FeatureExtractor
    part_type: str
    records: List[Dict[str, Any]]
    schema_version: str = CANONICAL_FEATURE_SCHEMA

    def get(self, feature_id: str) -> Optional[Dict[str, Any]]:
        return next((record for record in self.records if record.get("id") == feature_id), None)


def extract_canonical_features(shape, part_type: Optional[str] = None) -> CanonicalFeatureSet:
    """Run exactly the same extractor and record builder used by the main UI."""
    feature_extractor = FeatureExtractor(shape)
    resolved_part_type = part_type or PartClassifier().classify(feature_extractor, None)
    raw_records = build_feature_records(feature_extractor, resolved_part_type)
    records = normalize_canonical_feature_records(raw_records)

    feature_ids = [str(record.get("id") or "") for record in records]
    if any(not feature_id for feature_id in feature_ids):
        raise ValueError("Canonical feature records must all have a non-empty id")
    if len(feature_ids) != len(set(feature_ids)):
        raise ValueError("Canonical feature IDs remain duplicated after normalization")

    return CanonicalFeatureSet(
        feature_extractor=feature_extractor,
        part_type=resolved_part_type,
        records=records,
    )


def normalize_canonical_feature_records(raw_records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return copied records with stable, unique IDs in their existing order."""
    records = []
    id_counts: Counter[str] = Counter()
    for raw_record in raw_records:
        record = copy.deepcopy(raw_record)
        source_id = str(record.get("id") or "")
        id_counts[source_id] += 1
        if id_counts[source_id] > 1:
            record["canonical_source_id"] = source_id
            record["id"] = f"{source_id}__{id_counts[source_id]:02d}"
        records.append(record)

    return records
