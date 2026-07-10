"""Shared paths, config, and small helpers for Project Bergen."""
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "out"


def load_config():
    with open(ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_out(*subdirs):
    p = OUT.joinpath(*subdirs)
    p.mkdir(parents=True, exist_ok=True)
    return p


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_jsonl(path: Path):
    if not path.exists():
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def load_json_dir(directory: Path):
    """Load every .json file in a directory, sorted by filename."""
    items = []
    for p in sorted(Path(directory).glob("*.json")):
        with open(p) as f:
            items.append(json.load(f))
    return items
