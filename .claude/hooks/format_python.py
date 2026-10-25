"""PostToolUse hook: format and lint-fix a Python file after Claude edits it.

Reads the hook JSON from stdin. Silently does nothing if the file isn't Python
or if uv/ruff aren't installed yet (before task P0-01), so it never blocks work.
"""

import json
import shutil
import subprocess
import sys


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except json.JSONDecodeError:
        return 0
    path = (payload.get("tool_input") or {}).get("file_path", "")
    if not path.endswith(".py") or shutil.which("uv") is None:
        return 0
    for args in (["ruff", "format", path], ["ruff", "check", "--fix", "--quiet", path]):
        subprocess.run(["uv", "run", "--quiet", *args], capture_output=True, check=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
