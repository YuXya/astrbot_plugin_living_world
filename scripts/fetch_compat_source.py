"""Cache hash-verified official sources for offline compatibility tests."""

import hashlib
import json
import urllib.request
from pathlib import Path

BASE_URL = "https://raw.githubusercontent.com/AstrBotDevs/AstrBot/v4.27.5/astrbot/core/"
SOURCES = {
    "persona_mgr.py": "14bfc9413f37ce3ab09e1a11233525874929b6707dcd41ee4c554f3b83633606",
    "provider/sources/openai_source.py": "cd648baf5ab92357e3cb08326dcf4313662906f1e2d04959934762a299b72d13",
    "provider/sources/openai_responses_source.py": "78a0a2e6af3fb3d8c0e5c075bae9a578cc74733421da9e5886112fdf4dfc8a6d",
}


def fetch():
    directory = Path(__file__).resolve().parents[1] / "dist" / "compat"
    directory.mkdir(parents=True, exist_ok=True)
    manifest = []
    for path, digest in SOURCES.items():
        target = directory / ("astrbot-4.27.5-" + Path(path).name)
        body = target.read_bytes() if target.exists() else b""
        if hashlib.sha256(body).hexdigest() != digest:
            with urllib.request.urlopen(BASE_URL + path, timeout=30) as response:
                body = response.read()
            if hashlib.sha256(body).hexdigest() != digest:
                raise ValueError(
                    "Official source hash changed; review before using it in compatibility tests"
                )
            target.write_bytes(body)
        manifest.append({"url": BASE_URL + path, "sha256": digest, "file": target.name})
        print(target)
    (directory / "source.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")


if __name__ == "__main__":
    fetch()
