import json, os
from datetime import datetime
from app.experiments.schemas import ExperimentRun

_PATH = os.path.join(os.path.dirname(__file__), "experiments.json")

def load_runs() -> list[dict]:
    if not os.path.exists(_PATH):
        return []
    with open(_PATH) as f:
        return json.load(f)

def save_run(run: ExperimentRun) -> dict:
    runs = load_runs()
    entry = run.model_dump()
    entry["timestamp"] = datetime.utcnow().isoformat()
    runs.append(entry)
    with open(_PATH, "w") as f:
        json.dump(runs, f, indent=2)
    return entry