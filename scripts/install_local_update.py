"""Install a reviewed source package and compact only redundant debug snapshots."""

import argparse
import subprocess
import sys
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--plugin-dir", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    args = parser.parse_args()
    plugin = args.plugin_dir.resolve(strict=True)
    if plugin.name != "astrbot_plugin_living_world" or not (plugin / "metadata.yaml").is_file():
        raise ValueError("Target must be the existing Living World plugin directory")
    with zipfile.ZipFile(args.package.resolve(strict=True)) as archive:
        if archive.testzip() is not None:
            raise ValueError("Package integrity check failed")
        writes = []
        for entry in archive.infolist():
            destination = (plugin / entry.filename).resolve()
            if not destination.is_relative_to(plugin) or entry.is_dir():
                raise ValueError("Package contains an unsupported path")
            writes.append((destination, archive.read(entry)))
        for destination, content in writes:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(content)
    for name in ("life_migration", "drives_migration"):
        targets = [plugin / "living_world" / f"{name}.py"]
        targets.extend((plugin / "living_world" / "__pycache__").glob(f"{name}.*.pyc"))
        for target in targets:
            target = target.resolve()
            if not target.is_relative_to(plugin):
                raise ValueError("Obsolete module path escapes plugin directory")
            target.unlink(missing_ok=True)
    print(f"Installed {len(writes)} source files into {plugin}", flush=True)
    subprocess.run(
        [
            sys.executable,
            str(Path(__file__).with_name("compact_debug_snapshots.py")),
            "--database",
            str(args.database.resolve(strict=True)),
            "--apply",
        ],
        check=True,
    )
    print("Source update and debug compaction complete. Reload the plugin to use the new code.")


if __name__ == "__main__":
    main()
