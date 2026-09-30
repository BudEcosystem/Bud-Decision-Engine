"""Where the studio keeps its own files (never the model weights, which live in the Hugging Face cache)."""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# The desktop app keeps its data in the user's application-data folder and sets BASAL_DATA; a source checkout uses ./data.
DATA = Path(os.environ["BASAL_DATA"]).expanduser() if os.environ.get("BASAL_DATA") else ROOT / "data"
LOGS = DATA / "logs"
UPLOADS = DATA / "uploads"
UI = ROOT / "ui"

# How the studio starts background processes (model workers, downloads): in their own session on Linux and macOS so
# they can be stopped as a group, and without a console window on Windows.
if os.name == "nt":
    import subprocess as _sp
    DETACHED = {"creationflags": _sp.CREATE_NO_WINDOW | _sp.CREATE_NEW_PROCESS_GROUP}
    NO_WINDOW = {"creationflags": _sp.CREATE_NO_WINDOW}   # short helper commands such as nvidia-smi
else:
    DETACHED = {"start_new_session": True}
    NO_WINDOW = {}

for d in (DATA, LOGS, UPLOADS):
    d.mkdir(parents=True, exist_ok=True)
