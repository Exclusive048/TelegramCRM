from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INCLUDE_SUFFIXES = {".py", ".toml", ".md"}
EXCLUDE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "__pycache__",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
    "node_modules",
}


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() not in INCLUDE_SUFFIXES:
            continue
        if any(part in EXCLUDE_DIRS for part in path.parts):
            continue
        files.append(path)
    return files


def check_file(path: Path) -> str | None:
    data = path.read_bytes()
    if data.startswith(b"\xef\xbb\xbf"):
        return "utf-8 BOM"
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "not utf-8"
    return None


def main() -> int:
    failed: list[str] = []
    for path in iter_files(ROOT):
        error = check_file(path)
        if error:
            failed.append(f"{path}: {error}")

    if failed:
        print("Encoding check failed:")
        for item in failed:
            print(f"- {item}")
        return 1

    print("Encoding check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
