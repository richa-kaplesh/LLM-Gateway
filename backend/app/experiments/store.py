import json, os, tempfile, threading
from datetime import datetime, timezone
from app.experiments.schema import ExperimentRun
import logging
log = logging.getLogger(__name__)

_PATH = os.path.join(os.path.dirname(__file__), "experiments.json")
_lock = threading.Lock()

def load_runs() -> list[dict]:
    if not os.path.exists(_PATH):
        return []
    try:
        with open(_PATH, encoding="utf-8") as f:
            return json.load(f)
    except json.JSONDecodeError:
        log.warning("experiments.json was corrupt; moved to %s.corrupt", _PATH)
        os.replace(_PATH, _PATH + ".corrupt")   # keep the broken file, don't overwrite it
        return []

def save_run(run: ExperimentRun) -> dict:
    entry = run.model_dump()
    entry["timestamp"] = datetime.now(timezone.utc).isoformat()
    with _lock:                                  # one writer at a time
        runs = load_runs()
        runs.append(entry)
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(_PATH), suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(runs, f, indent=2)
        os.replace(tmp, _PATH)                   # swap the finished file into place
    return entry