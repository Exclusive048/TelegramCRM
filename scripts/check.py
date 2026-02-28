from __future__ import annotations

import subprocess
import sys


COMMANDS: list[tuple[str, list[str]]] = [
    ("lint", [sys.executable, "-m", "ruff", "check", "."]),
    ("typecheck", [sys.executable, "-m", "mypy", "app", "main.py"]),
    ("encoding", [sys.executable, "-m", "scripts.check_encoding"]),
    ("migrations", [sys.executable, "-m", "scripts.migrations_check"]),
    ("smoke", [sys.executable, "-m", "scripts.smoke"]),
]


def main() -> int:
    for name, cmd in COMMANDS:
        print(f"==> {name}")
        result = subprocess.run(cmd)
        if result.returncode != 0:
            return result.returncode
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
