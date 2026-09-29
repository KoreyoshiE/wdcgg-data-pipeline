"""Create the public file-integrity manifest without including Git internals."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
DESTINATION = ROOT / "provenance" / "RELEASE_MANIFEST.json"


def role(path: str) -> str:
    if path.startswith("src/") or path.startswith("scripts/"):
        return "scientific_or_reproduction_code"
    if path.startswith("configs/"):
        return "frozen_configuration"
    if path.startswith("results/"):
        return "frozen_scientific_result"
    if path.startswith("provenance/") or path.startswith("data/manifests/"):
        return "source_or_study_provenance"
    if path.startswith("data/examples/") or path.startswith("tests/"):
        return "synthetic_fixture_or_test"
    if path.startswith(".github/"):
        return "continuous_integration"
    if path in {"pyproject.toml", "requirements.txt"}:
        return "package_environment"
    if path == "LICENSE":
        return "software_license"
    return "research_documentation"


def main() -> None:
    listing = subprocess.check_output(["git", "ls-files", "--cached", "-z"],
                                      cwd=ROOT).decode("utf-8").split("\0")
    records = []
    for relative in sorted(set(listing)):
        if not relative or relative == "provenance/RELEASE_MANIFEST.json":
            continue
        path = ROOT / relative
        if not path.is_file():
            continue
        blob = subprocess.check_output(["git", "show", f":{relative}"], cwd=ROOT)
        records.append({"path": relative, "size_bytes": len(blob),
                        "sha256": hashlib.sha256(blob).hexdigest(),
                        "role": role(relative), "version": "1.1.0"})
    payload = {"release_version": "1.1.0", "scientific_evidence_state": "FROZEN",
               "generated_utc": datetime.now(timezone.utc).isoformat(),
               "file_count": len(records), "files": records,
               "hash_scope": "Canonical Git blob bytes; text checkout line endings may vary by platform",
               "self_reference": "This manifest omits its own hash."}
    DESTINATION.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(f"{len(records)} public scientific, configuration, result and documentation files")


if __name__ == "__main__":
    main()
