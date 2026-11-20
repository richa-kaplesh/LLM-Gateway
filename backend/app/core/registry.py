import logging
import os
import re
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

import yaml
from dotenv import dotenv_values
from pydantic import ValidationError

from app.models.deployment import Deployment, DeploymentSpec

log = logging.getLogger(__name__)

BACKEND_DIR = Path(__file__).resolve().parents[2]
DEPLOYMENTS_FILE = BACKEND_DIR / "deployments.yaml"


class RegistryError(Exception):
    pass


def _read_entries(path: Path) -> list:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise RegistryError(f"{path} not found")
    except yaml.YAMLError as e:
        raise RegistryError(f"{path} is not valid YAML: {e}")
    if not isinstance(data, dict) or not isinstance(data.get("deployments"), list):
        raise RegistryError(f"{path} must contain a top-level 'deployments' list")
    return data["deployments"]


def _parse_specs(entries: list, problems: list[str]) -> list[DeploymentSpec]:
    specs = []
    for index, entry in enumerate(entries):
        label = entry.get("id", f"#{index}") if isinstance(entry, dict) else f"#{index}"
        try:
            specs.append(DeploymentSpec.model_validate(entry))
        except ValidationError as e:
            for err in e.errors():
                field = ".".join(str(part) for part in err["loc"]) or "(entry)"
                problems.append(f"{label}: {field}: {err['msg']}")
    return specs


def _check_unique_ids(specs: list[DeploymentSpec], problems: list[str]) -> None:
    seen = set()
    for spec in specs:
        if spec.id in seen:
            problems.append(f"{spec.id}: duplicate id")
        seen.add(spec.id)


_NUMBERED = re.compile(r"^(?P<base>.+)_(?P<n>\d+)$")


def _split_keys(value: str) -> list[str]:
    keys: list[str] = []
    for part in re.split(r"[,;\s]+", value or ""):
        key = part.strip().strip("\"'`[]()")
        if key and key not in keys:
            keys.append(key)
    return keys


def _lookup_key(env: Mapping[str, str], name: str) -> str:
    single = _split_keys(env.get(name) or "")
    if single:
        return single[0]
    numbered = _NUMBERED.match(name)
    if not numbered:
        return ""
    keys = _split_keys(env.get(numbered["base"] + "S") or "")
    index = int(numbered["n"]) - 1
    return keys[index] if 0 <= index < len(keys) else ""


def _resolve_keys(specs: list[DeploymentSpec], env: Mapping[str, str],
                  problems: list[str]) -> list[Deployment]:
    deployments = []
    for spec in specs:
        if not spec.enabled:
            continue
        key = _lookup_key(env, spec.key_env)
        if not key:
            numbered = _NUMBERED.match(spec.key_env)
            fallback = f" (or item {numbered['n']} of {numbered['base']}S)" if numbered else ""
            problems.append(f"{spec.id}: env var {spec.key_env}{fallback} is missing or empty")
            continue
        deployments.append(Deployment(**spec.model_dump(by_alias=True), api_key=key))
    return deployments


def load_deployments(path: Path, env: Mapping[str, str]) -> list[Deployment]:
    problems: list[str] = []
    specs = _parse_specs(_read_entries(path), problems)
    _check_unique_ids(specs, problems)
    deployments = _resolve_keys(specs, env, problems)
    if not problems and not deployments:
        problems.append("no enabled deployments")
    if problems:
        raise RegistryError(f"invalid {path.name}:\n  " + "\n  ".join(problems))
    return deployments


def _process_env() -> dict[str, str]:
    file_values = {k: v for k, v in dotenv_values(BACKEND_DIR / ".env").items() if v is not None}
    return {**file_values, **os.environ}


@lru_cache()
def get_deployments() -> tuple[Deployment, ...]:
    deployments = load_deployments(DEPLOYMENTS_FILE, _process_env())
    for d in deployments:
        log.info("deployment loaded: id=%s provider=%s model=%s limits=%s",
                 d.id, d.provider, d.model, d.limits.model_dump())
    return tuple(deployments)
