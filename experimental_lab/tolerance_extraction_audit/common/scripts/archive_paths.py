"""Locate frozen historical inputs without putting new output in the archive."""
from pathlib import Path


def historical_input(relative: str) -> Path:
    root = Path(__file__).resolve().parents[2]
    path = root / "experiments" / relative
    if path.exists():
        return path
    matches = sorted((root / "ARCHIVE").glob(f"*/experiments/{relative}"))
    if len(matches) != 1:
        raise FileNotFoundError(f"Expected one historical input: {relative}; found {len(matches)}")
    return matches[0]
