from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name("ENGINE_MANIFEST.json")


def main() -> int:
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    missing = []
    for capability in data.get("capabilities", []):
        module = capability.get("module")
        if module and not (ROOT / module).is_file():
            missing.append({"id": capability.get("id"), "module": module})
    entrypoint = data.get("entrypoint", "")
    if not (ROOT / entrypoint).is_file():
        missing.append({"id": "entrypoint", "module": entrypoint})
    if missing:
        print(json.dumps({"valid": False, "missing": missing}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps({
        "valid": True,
        "engine_version": data["engine_version"],
        "capability_count": len(data.get("capabilities", [])),
        "known_gap_count": len(data.get("known_gaps", [])),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
