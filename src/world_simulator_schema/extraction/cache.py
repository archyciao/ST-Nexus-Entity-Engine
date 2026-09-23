"""Atomic task cache scoped by the actual request, model settings and source context."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import tempfile


def atomic_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".pending-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class TaskCache:
    def __init__(self, directory: Path | None, context=""):
        self.directory, self.context = directory, context

    def run(self, runner, **kwargs):
        request = {key: value for key, value in kwargs.items() if not callable(value) and key not in {"api_key", "normalizer", "validator"}}
        # Different accounts must not share cached replies; only the digest is persisted.
        request["credential_fingerprint"] = hashlib.sha256(kwargs.get("api_key", "").encode()).hexdigest()
        key = hashlib.sha256(json.dumps(["nexus-extraction-3", self.context, request], ensure_ascii=False, sort_keys=True).encode()).hexdigest()
        path = self.directory / (key + ".json") if self.directory else None
        if path and path.exists():
            try:
                cached = json.loads(path.read_text(encoding="utf-8"))
                if cached.get("ok"):
                    return {**cached, "cache_hit": True}
            except (OSError, ValueError):
                pass
        result = runner(**kwargs)
        if path and result.get("ok"):
            secret = kwargs.get("api_key", "")
            if secret and secret in json.dumps(result, ensure_ascii=False):
                raise RuntimeError("结果意外包含认证信息，未保存缓存")
            atomic_json(path, result)
        return result
