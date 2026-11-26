"""
Per-project policy loader for Motta Guardrails.

Policy files live at ~/.hermes/guardrails/<project>.yaml (or
$HERMES_HOME/guardrails/<project>.yaml when HERMES_HOME is set). Each
module is responsible for reading only the keys it cares about from the
returned dict; this loader only handles file resolution, YAML parsing,
and the top-level schema shape — it does not know about any specific
module's config.

Schema (top-level keys; all optional unless noted):

    project: str                  # required, must match the filename stem
    scope:
      allowed_topics: [str]
      forbidden_topics: [str]
      canonical_fallback: str
    cost:
      ceiling_per_session_usd: float
      ceiling_per_day_usd: float
    judge:
      enabled: bool
      model: str
      provider: str
      threshold: float
    channels:
      <channel_name>:
        <any of the above keys, overriding the project default for that channel>
"""

import os
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - yaml is a Hermes core dependency already
    yaml = None

_REQUIRED_TOP_LEVEL = ("project",)


class PolicyError(ValueError):
    pass


def _guardrails_dir() -> Path:
    hermes_home = os.environ.get("HERMES_HOME")
    base = Path(hermes_home) if hermes_home else Path.home() / ".hermes"
    return base / "guardrails"


def policy_path(project: str) -> Path:
    return _guardrails_dir() / f"{project}.yaml"


def load_policy(project: str) -> dict:
    if yaml is None:
        raise PolicyError("PyYAML is not available in this environment")

    path = policy_path(project)
    if not path.exists():
        raise PolicyError(f"No policy file for project '{project}' at {path}")

    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    if not isinstance(data, dict):
        raise PolicyError(f"Policy file {path} must contain a YAML mapping at the top level")

    for key in _REQUIRED_TOP_LEVEL:
        if key not in data:
            raise PolicyError(f"Policy file {path} is missing required key '{key}'")

    if data["project"] != project:
        raise PolicyError(
            f"Policy file {path} declares project='{data['project']}' but was loaded for '{project}'"
        )

    return data


def resolve_for_channel(policy: dict, channel: str = None) -> dict:
    """Merge a channel-specific override on top of the project defaults.
    Channel keys only override the top-level keys they explicitly set;
    everything else falls back to the project default.
    """
    if not channel:
        return policy
    overrides = (policy.get("channels") or {}).get(channel)
    if not overrides:
        return policy
    merged = dict(policy)
    for key, value in overrides.items():
        if key == "channels":
            continue
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged
