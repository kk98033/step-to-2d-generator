"""Evidence-graded pairing of historical 2D drawings and 3D models.

The resolver is intentionally conservative.  A pair is eligible for automatic
feature verification only when either its complete filename stem is identical,
or a unique STEP and DXF share the same embedded company part number and exact
revision designator.  Cross-revision and ambiguous matches remain visible in
the manifest but are never promoted to training/retrieval evidence.
"""

from __future__ import annotations

import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


PART_REVISION_PATTERN = re.compile(
    r"(?<![A-Z0-9])(?P<part>[0-3][A-Z0-9]{9})"
    r"(?:[-_]?(?P<revision_kind>[RA])(?P<revision_number>\d+))?"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PartIdentity:
    part_number: str
    revision_kind: Optional[str]
    revision_number: Optional[int]

    @property
    def revision(self) -> Optional[str]:
        if self.revision_kind is None or self.revision_number is None:
            return None
        return f"{self.revision_kind}{self.revision_number:02d}"

    @property
    def part_revision_key(self) -> Optional[Tuple[str, str]]:
        if self.revision is None:
            return None
        return self.part_number, self.revision


def parse_part_identity(path_or_name: str) -> Optional[PartIdentity]:
    """Extract the embedded ten-character company part number and revision."""
    stem = os.path.splitext(os.path.basename(path_or_name))[0]
    matches = list(PART_REVISION_PATTERN.finditer(stem))
    if len(matches) != 1:
        return None
    match = matches[0]
    kind = match.group("revision_kind")
    number = match.group("revision_number")
    return PartIdentity(
        part_number=match.group("part").upper(),
        revision_kind=kind.upper() if kind else None,
        revision_number=int(number) if number is not None else None,
    )


def _normalized_stem(path: str) -> str:
    return os.path.splitext(os.path.basename(path))[0].lower().strip()


def _pair_record(
    step_path: str,
    dxf_path: str,
    method: str,
    identity: Optional[PartIdentity],
    checks: Sequence[str],
) -> Dict[str, Any]:
    step_stem = _normalized_stem(step_path)
    dxf_stem = _normalized_stem(dxf_path)
    return {
        "pair_id": f"{method}:{dxf_stem}",
        "step_path": os.path.abspath(step_path),
        "dxf_path": os.path.abspath(dxf_path),
        "pair_method": method,
        "eligible_for_auto_verification": True,
        "part_number": identity.part_number if identity else None,
        "revision": identity.revision if identity else None,
        "step_stem": step_stem,
        "dxf_stem": dxf_stem,
        "verification_checks": list(checks),
    }


def build_pair_manifest(
    step_paths: Iterable[str],
    dxf_paths: Iterable[str],
) -> Dict[str, Any]:
    """Resolve trustworthy pairs and retain rejected possibilities for audit."""
    steps = sorted({os.path.abspath(path) for path in step_paths}, key=str.lower)
    dxfs = sorted({os.path.abspath(path) for path in dxf_paths}, key=str.lower)
    step_by_stem: Dict[str, List[str]] = defaultdict(list)
    dxf_by_stem: Dict[str, List[str]] = defaultdict(list)
    for path in steps:
        step_by_stem[_normalized_stem(path)].append(path)
    for path in dxfs:
        dxf_by_stem[_normalized_stem(path)].append(path)

    verified_pairs: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    consumed_steps: set[str] = set()
    consumed_dxfs: set[str] = set()

    # Full-stem equality is the strongest available filename evidence.
    for stem in sorted(set(step_by_stem) & set(dxf_by_stem)):
        stem_steps = step_by_stem[stem]
        stem_dxfs = dxf_by_stem[stem]
        if len(stem_steps) == 1 and len(stem_dxfs) == 1:
            step_path, dxf_path = stem_steps[0], stem_dxfs[0]
            identity = parse_part_identity(dxf_path) or parse_part_identity(step_path)
            verified_pairs.append(_pair_record(
                step_path,
                dxf_path,
                "EXACT_FILENAME",
                identity,
                ["exact_step_dxf_filename"],
            ))
            consumed_steps.add(step_path)
            consumed_dxfs.add(dxf_path)
        else:
            candidates.append({
                "status": "AMBIGUOUS_EXACT_FILENAME",
                "stem": stem,
                "step_paths": stem_steps,
                "dxf_paths": stem_dxfs,
            })

    step_by_part_revision: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    dxf_by_part_revision: Dict[Tuple[str, str], List[str]] = defaultdict(list)
    step_by_part: Dict[str, List[str]] = defaultdict(list)
    dxf_by_part: Dict[str, List[str]] = defaultdict(list)

    for path in steps:
        identity = parse_part_identity(path)
        if identity:
            step_by_part[identity.part_number].append(path)
            if identity.part_revision_key:
                step_by_part_revision[identity.part_revision_key].append(path)
    for path in dxfs:
        identity = parse_part_identity(path)
        if identity:
            dxf_by_part[identity.part_number].append(path)
            if identity.part_revision_key:
                dxf_by_part_revision[identity.part_revision_key].append(path)

    # A unique part/revision match permits prefixes such as COVER_ or *_ASSY.
    for key in sorted(set(step_by_part_revision) & set(dxf_by_part_revision)):
        available_steps = [path for path in step_by_part_revision[key] if path not in consumed_steps]
        available_dxfs = [path for path in dxf_by_part_revision[key] if path not in consumed_dxfs]
        if not available_steps or not available_dxfs:
            continue
        if len(available_steps) == 1 and len(available_dxfs) == 1:
            step_path, dxf_path = available_steps[0], available_dxfs[0]
            identity = parse_part_identity(dxf_path)
            verified_pairs.append(_pair_record(
                step_path,
                dxf_path,
                "EMBEDDED_PART_EXACT_REVISION",
                identity,
                [
                    "embedded_part_number_match",
                    "exact_revision_designator_match",
                    "unique_unconsumed_step_for_part_revision",
                    "unique_unconsumed_dxf_for_part_revision",
                ],
            ))
            consumed_steps.add(step_path)
            consumed_dxfs.add(dxf_path)
        else:
            candidates.append({
                "status": "AMBIGUOUS_PART_REVISION",
                "part_number": key[0],
                "revision": key[1],
                "step_paths": available_steps,
                "dxf_paths": available_dxfs,
            })

    # Same part but no exact revision is useful for review, never auto-training.
    for part_number in sorted(set(step_by_part) & set(dxf_by_part)):
        remaining_steps = [path for path in step_by_part[part_number] if path not in consumed_steps]
        remaining_dxfs = [path for path in dxf_by_part[part_number] if path not in consumed_dxfs]
        if remaining_steps and remaining_dxfs:
            candidates.append({
                "status": "CROSS_REVISION_OR_MISSING_REVISION",
                "part_number": part_number,
                "step_paths": remaining_steps,
                "dxf_paths": remaining_dxfs,
            })

    pair_method_counts = Counter(pair["pair_method"] for pair in verified_pairs)
    candidate_status_counts = Counter(item["status"] for item in candidates)
    parsed_step_count = sum(parse_part_identity(path) is not None for path in steps)
    parsed_dxf_count = sum(parse_part_identity(path) is not None for path in dxfs)
    return {
        "schema_version": 1,
        "policy": {
            "auto_verification_methods": ["EXACT_FILENAME", "EMBEDDED_PART_EXACT_REVISION"],
            "cross_revision_auto_verification": False,
            "ambiguous_pair_auto_verification": False,
        },
        "verified_pairs": verified_pairs,
        "candidates": candidates,
        "unpaired_step_paths": [path for path in steps if path not in consumed_steps],
        "unpaired_dxf_paths": [path for path in dxfs if path not in consumed_dxfs],
        "statistics": {
            "step_files": len(steps),
            "dxf_files": len(dxfs),
            "parsed_step_identities": parsed_step_count,
            "parsed_dxf_identities": parsed_dxf_count,
            "verified_pair_count": len(verified_pairs),
            "verified_pair_methods": dict(pair_method_counts),
            "candidate_count": len(candidates),
            "candidate_statuses": dict(candidate_status_counts),
            "unpaired_step_count": len(steps) - len(consumed_steps),
            "unpaired_dxf_count": len(dxfs) - len(consumed_dxfs),
        },
    }
