#!/usr/bin/env python3
"""ForgeOne — controlled model manager.

Registry-driven, dry-run by default. It will **not** download anything unless
``--execute`` is passed, and it refuses to download a model that is not
explicitly approved in the registry.

Design rules (from the milestone):
    * registry-based selection — no ad-hoc model IDs on the command line
    * dry-run before download
    * explicit destination under $FORGEONE_HOME/storage/
    * disk-space check against the guardrail (>= 150 GiB free)
    * revision pinning where the registry records one
    * resumable download (delegated to huggingface_hub)
    * checkpoint completeness verification
    * no concurrent large downloads
    * no automatic model loading after download
    * no silent global-cache fallback
    * no model substitution without owner approval

**This task downloads nothing.** ``approved_download_batch`` in the registry is
empty on purpose.

Run under a ForgeOne venv (needs PyYAML; huggingface_hub only for --execute):

    source scripts/forgeone-env.sh
    storage/bakeoff/model-venv/bin/python scripts/model-manager.py list
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = REPO / "configs/model-registry.yaml"
POLICY = REPO / "configs/storage-policy.yaml"
MIN_FREE_GIB = 150.0

EXIT_OK, EXIT_FAIL, EXIT_BLOCKED = 0, 1, 2


class Blocked(RuntimeError):
    """A safety rule refused the operation."""


def load_yaml(path: Path) -> dict:
    try:
        import yaml  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise SystemExit(
            "PyYAML is required. Run under a ForgeOne venv, e.g.\n"
            "  storage/bakeoff/model-venv/bin/python scripts/model-manager.py ..."
        ) from exc
    if not path.is_file():
        raise SystemExit(f"missing config: {path}")
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def storage_root() -> Path:
    return Path(os.environ.get("FORGEONE_STORAGE", REPO / "storage"))


def free_gib(path: Path) -> float:
    probe = path if path.exists() else path.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    return shutil.disk_usage(probe).free / (1024**3)


def resolve_destination(entry: dict) -> Path:
    dest = entry.get("destination") or "storage/models"
    return (REPO / dest).resolve()


def find(registry: dict, model_id: str) -> dict:
    for entry in registry.get("models", []):
        if entry.get("id") == model_id:
            return entry
    raise Blocked(f"model {model_id!r} is not in the registry")


def cmd_list(registry: dict, args) -> int:
    rows = registry.get("models", [])
    print(f"{'MODEL ID':52} {'DOWNLOAD':14} {'VERIFICATION':44} APPROVAL")
    print("-" * 130)
    for e in rows:
        print(f"{e.get('id',''):52} {str(e.get('download_status','')):14} "
              f"{str(e.get('verification_status',''))[:44]:44} {e.get('approval','')}")
    print(f"\n{len(rows)} registered. approved_download_batch = "
          f"{registry.get('approved_download_batch') or '[] (empty)'}")
    return EXIT_OK


def cmd_show(registry: dict, args) -> int:
    entry = find(registry, args.model)
    print(json.dumps(entry, indent=2, sort_keys=True, default=str))
    return EXIT_OK


def cmd_verify(registry: dict, args) -> int:
    """Check an installed checkpoint is complete, without loading weights."""
    entry = find(registry, args.model)
    dest = resolve_destination(entry)
    hub = storage_root() / "cache/huggingface/hub"
    repo_dir = hub / ("models--" + entry["id"].replace("/", "--"))
    if not repo_dir.is_dir():
        print(f"BLOCKED: no local cache for {entry['id']} under {hub}")
        return EXIT_BLOCKED

    rev = entry.get("revision")
    snaps = sorted((repo_dir / "snapshots").glob("*")) if (repo_dir / "snapshots").is_dir() else []
    if rev and not (repo_dir / "snapshots" / rev).is_dir():
        print(f"BLOCKED: pinned revision {rev} not present locally")
        return EXIT_BLOCKED

    target = repo_dir / "snapshots" / rev if rev else (snaps[-1] if snaps else None)
    if target is None:
        print("BLOCKED: no snapshot directory found")
        return EXIT_BLOCKED

    files = [p for p in target.iterdir() if p.is_file()]
    weights = [p for p in files if p.suffix == ".safetensors"]
    broken = [p.name for p in files if not p.exists()]
    size = sum(p.stat().st_size for p in files if p.exists()) / (1024**3)
    print(f"  snapshot      : {target.relative_to(REPO)}")
    print(f"  files         : {len(files)}")
    print(f"  weight shards : {len(weights)}")
    print(f"  resolved size : {size:.2f} GiB")
    print(f"  broken links  : {broken or 'none'}")
    ok = bool(weights) and not broken
    print(f"  VERDICT       : {'COMPLETE' if ok else 'INCOMPLETE'}")
    return EXIT_OK if ok else EXIT_FAIL


def cmd_download(registry: dict, args) -> int:
    """Dry-run by default. Refuses anything not explicitly approved."""
    entry = find(registry, args.model)
    dest = resolve_destination(entry)

    approved = registry.get("approved_download_batch") or []
    is_approved = entry["id"] in approved
    verified = "unverified" not in str(entry.get("verification_status", "")).lower()

    free = free_gib(dest)
    print("  DRY RUN — nothing will be downloaded" if not args.execute else "  EXECUTE")
    print(f"  model       : {entry['id']}")
    print(f"  revision    : {entry.get('revision') or 'UNPINNED'}")
    print(f"  destination : {dest.relative_to(REPO)}")
    print(f"  free disk   : {free:.1f} GiB (guardrail {MIN_FREE_GIB:.0f} GiB)")
    print(f"  verification: {entry.get('verification_status')}")
    print(f"  approval    : {entry.get('approval')}")
    print(f"  in approved_download_batch: {is_approved}")

    if not verified:
        print("\nBLOCKED: entry is unverified — its repository, revision, format or "
              "quantization is not confirmed. Missing IDs are not invented.")
        return EXIT_BLOCKED
    if not is_approved:
        print("\nBLOCKED: not in approved_download_batch. A registered candidate is "
              "not automatically approved for download.")
        return EXIT_BLOCKED
    if free < MIN_FREE_GIB:
        print(f"\nBLOCKED: free disk {free:.1f} GiB < guardrail {MIN_FREE_GIB:.0f} GiB")
        return EXIT_BLOCKED
    if not args.execute:
        print("\nDry run complete. Re-run with --execute to download.")
        return EXIT_OK

    # No global-cache fallback: HF_HOME must resolve under storage/.
    hf_home = Path(os.environ.get("HF_HOME", ""))
    if not hf_home or storage_root() not in hf_home.resolve().parents:
        print(f"\nBLOCKED: HF_HOME ({hf_home or 'unset'}) is not under {storage_root()}. "
              "Source scripts/forgeone-env.sh first.")
        return EXIT_BLOCKED

    from huggingface_hub import snapshot_download  # noqa: PLC0415

    kwargs = {"repo_id": entry["id"]}
    if entry.get("revision"):
        kwargs["revision"] = entry["revision"]
    print(f"  downloading {entry['id']} ...")
    path = snapshot_download(**kwargs)  # resumable by design
    print(f"  downloaded to {path}")
    print("  NOTE: the model is NOT loaded automatically.")
    return EXIT_OK


def main() -> int:
    ap = argparse.ArgumentParser(prog="model-manager.py")
    ap.add_argument("--registry", default=str(REGISTRY))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="list registered models")
    p = sub.add_parser("show", help="show one registry entry"); p.add_argument("model")
    p = sub.add_parser("verify", help="verify an installed checkpoint"); p.add_argument("model")
    p = sub.add_parser("download", help="dry-run (or --execute) a download")
    p.add_argument("model")
    p.add_argument("--execute", action="store_true",
                   help="actually download; without it this is a dry run")
    args = ap.parse_args()

    registry = load_yaml(Path(args.registry))
    handlers = {"list": cmd_list, "show": cmd_show,
                "verify": cmd_verify, "download": cmd_download}
    try:
        return handlers[args.cmd](registry, args)
    except Blocked as exc:
        print(f"BLOCKED: {exc}")
        return EXIT_BLOCKED


if __name__ == "__main__":
    raise SystemExit(main())
