"""Fetch the pinned ESCI files, checking upstream sizes and content hashes."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "config/esci-source.json"
DEFAULT_DATA_DIR = ROOT / "data/raw/esci"
CHUNK = 1024 * 1024


def verify_file(path: Path, spec: dict) -> dict:
    size = path.stat().st_size
    if size != spec["bytes"]:
        raise ValueError(f"Size mismatch for {path.name}: {size} != {spec['bytes']}")
    sha256 = hashlib.sha256()
    blob = hashlib.sha1(f"blob {size}\0".encode())
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            sha256.update(chunk)
            blob.update(chunk)
    if not (spec.get("sha256") or spec.get("git_blob_sha1")):
        raise ValueError(f"No expected checksum for {path.name}")
    for key, actual in (("sha256", sha256.hexdigest()), ("git_blob_sha1", blob.hexdigest())):
        if key in spec and spec[key] != actual:
            raise ValueError(f"{key} mismatch for {path.name}")
    return {"name": spec["name"], "bytes": size, "sha256": sha256.hexdigest()}


def download_file(spec: dict, data_dir: Path) -> dict:
    path = data_dir / spec["name"]
    if path.exists():
        result = verify_file(path, spec)
        print(f"Verified cached {path.name}", flush=True)
        return result
    partial = path.with_suffix(path.suffix + ".partial")
    print(f"Downloading {path.name} ({spec['bytes']:,} bytes)", flush=True)
    request = urllib.request.Request(spec["url"], headers={"User-Agent": "commerce-search-lab-data-audit"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, partial.open("wb") as output:
            downloaded = 0
            next_progress = 128 * CHUNK
            while chunk := response.read(CHUNK):
                output.write(chunk)
                downloaded += len(chunk)
                if downloaded > spec["bytes"]:
                    raise ValueError(f"Download exceeds pinned size for {path.name}")
                if downloaded >= next_progress:
                    print(f"  {downloaded / spec['bytes']:.0%}", flush=True)
                    next_progress += 128 * CHUNK
        result = verify_file(partial, spec)
        partial.replace(path)
        return result
    finally:
        partial.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    args = parser.parse_args()
    source = json.loads(SOURCE.read_text())
    args.data_dir.mkdir(parents=True, exist_ok=True)
    files = [download_file(spec, args.data_dir) for spec in source["files"]]
    manifest = {
        "repository": source["repository"],
        "revision": source["revision"],
        "verified_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": files,
    }
    (args.data_dir / "download-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    print("All pinned files verified.", flush=True)


if __name__ == "__main__":
    main()
