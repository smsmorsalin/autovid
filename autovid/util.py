import json
import os
import pathlib
import subprocess
import sys
import time

import yaml

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _merge(a, b):
    out = dict(a)
    for k, v in b.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_config(path=None):
    """Load YAML; a file may say `extends: other.yaml` to override only some keys."""
    p = pathlib.Path(path) if path else ROOT / "config.yaml"
    if not p.is_absolute() and not p.exists():
        p = ROOT / p
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    base = cfg.pop("extends", None)
    if base:
        cfg = _merge(load_config(p.parent / base), cfg)
    return cfg


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, file=sys.stderr, flush=True)


def env(name, required=True):
    v = os.environ.get(name, "")
    if required and not v:
        raise SystemExit(f"Missing environment variable {name}. See README (secrets).")
    return v


def run(cmd, **kw):
    r = subprocess.run([str(c) for c in cmd], capture_output=True, text=True, **kw)
    if r.returncode:
        raise RuntimeError(
            "command failed: " + " ".join(str(c) for c in cmd)[:400] + "\n" + r.stderr[-2500:]
        )
    return r


def media_duration(path):
    r = run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", path])
    return float(r.stdout.strip())


def read_json(path):
    return json.loads(pathlib.Path(path).read_text(encoding="utf-8"))


def write_json(path, obj):
    pathlib.Path(path).write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
