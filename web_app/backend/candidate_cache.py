"""Candidate cache identity and per-key admission, independent of CAD engines."""

from contextlib import contextmanager
import hashlib
from pathlib import Path
import threading


class CandidateCache:
    def __init__(self):
        self._guard = threading.Lock()
        self._locks = {}

    @contextmanager
    def single_flight(self, key):
        # Include waiters in the reference count so an active key cannot split
        # into two locks. Remove idle entries to bound registry memory.
        with self._guard:
            lock, users = self._locks.get(key, (threading.Lock(), 0))
            self._locks[key] = (lock, users + 1)
        try:
            with lock:
                yield
        finally:
            with self._guard:
                _, users = self._locks[key]
                if users == 1:
                    del self._locks[key]
                else:
                    self._locks[key] = (lock, users - 1)

    @staticmethod
    def signature(step_path, engine, project_root):
        root = Path(project_root)
        source = Path(step_path)
        # Hash file bytes: equal names, sizes or timestamps do not imply equal
        # models. Entries still live within each model's authorized directory.
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        sources = [root / "tools/cad_annotation_worker.py"]
        cad = root / "auto_2d_drawing"
        sources.extend(cad.glob("*.py"))
        sources.extend((cad / "smart_extractors").glob("*.py"))
        sources.extend((cad / "tolerance").glob("*.py"))
        if engine == "experimental_v026":
            lab = root / "experimental_lab/tolerance_extraction_audit"
            sources.extend((lab / "common/scripts").glob("*.py"))
            sources.append(lab / "DRAWING_CURRENT_FINAL/experimental_main.py")
        revision = hashlib.sha256()
        for path in sorted(sources):
            stat = path.stat()
            revision.update(f"{path.relative_to(root)}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
        return {
            "cache_schema_version": 1,
            "candidate_engine": engine,
            "step_sha256": digest.hexdigest(),
            "analysis_revision": revision.hexdigest(),
        }
