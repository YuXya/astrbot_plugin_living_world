"""Build a source-only AstrBot test package without runtime data or credentials."""

import hashlib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.4"


def build():
    files = [
        ROOT / name
        for name in (
            "main.py",
            "metadata.yaml",
            "requirements.txt",
            "README.md",
            "LICENSE",
            "CHANGELOG.md",
            "AGENTS.md",
            "pyproject.toml",
        )
    ]
    for directory, suffixes in (
        ("living_world", {".py"}),
        ("pages", {".html", ".js", ".css"}),
        ("docs", {".md"}),
    ):
        files.extend(
            path
            for path in (ROOT / directory).rglob("*")
            if path.is_file() and path.suffix in suffixes and "__pycache__" not in path.parts
        )
    output = ROOT / "dist" / f"astrbot_plugin_living_world-{VERSION}.zip"
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(files):
            info = zipfile.ZipInfo(path.relative_to(ROOT).as_posix())
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, path.read_bytes())
    with zipfile.ZipFile(output) as archive:
        assert archive.testzip() is None
        assert {"main.py", "metadata.yaml", "pages/living-world/index.html"} <= set(
            archive.namelist()
        )
        for name in archive.namelist():
            if name.endswith(".py"):
                compile(archive.read(name), name, "exec")
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(".sha256").write_text(f"{digest}  {output.name}\n", encoding="utf-8")
    print(f"{output}\n{len(files)} files, {output.stat().st_size} bytes\nSHA256 {digest}")


if __name__ == "__main__":
    build()
