"""Process working directory for the bundled runtime."""
import os
from pathlib import Path


def runtime_working_directory(cwd: Path) -> Path:
    if os.name != "nt" or os.environ.get("DSH_RUNTIME_MODE", "exe") != "exe":
        return cwd

    # The 0.2.2rc2 Windows loader only recognizes C:\snapshot and V:\snapshot.
    if cwd.drive.upper() in ("C:", "V:"):
        return Path(cwd.anchor.upper(), *cwd.parts[1:])
    for root in (Path("C:/"), Path("V:/")):
        if root.is_dir():
            return root
    return cwd
