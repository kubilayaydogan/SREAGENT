"""Ortam değişkenleri + YAML yapılandırması. Gizli değerler yalnız ortamdan okunur, hiçbir yere yazılmaz."""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path
from typing import Any

import yaml


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = _deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load_yaml_config(path: str | None = None) -> dict:
    base = yaml.safe_load(resources.files("airlab_agent").joinpath("defaults.yaml").read_text(encoding="utf-8"))
    path = path or os.getenv("AGENT_CONFIG")
    if path and Path(path).exists():
        base = _deep_merge(base, yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})
    return base


@dataclass
class Settings:
    dt_env: str = ""
    mcp_url: str = ""
    dt_token: str = field(default="", repr=False)
    anthropic_key: str = field(default="", repr=False)
    claude_model: str = "claude-sonnet-5-5"
    claude_rca_model: str = "claude-opus-5-5"   # RCA açıklaması (explain); yorumlama/sohbet claude_model'de kalır
    langsmith_key: str = field(default="", repr=False)
    langsmith_project: str = "airlab-sre-agent"
    langsmith_tracing: bool = False
    data_dir: Path = Path("./data")
    cfg: dict[str, Any] = field(default_factory=dict)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_key) and os.getenv("AGENT_DISABLE_LLM", "").lower() not in ("1", "true", "yes")

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def state_db(self) -> Path:
        return self.data_dir / "state.sqlite"

    @property
    def checkpoint_db(self) -> Path:
        return self.data_dir / "checkpoints.sqlite"

    def ensure_dirs(self) -> None:
        self.reports_dir.mkdir(parents=True, exist_ok=True)

    def secrets(self) -> list[str]:
        return [s for s in (self.dt_token, self.anthropic_key, self.langsmith_key) if s]


def load_settings(config_path: str | None = None) -> Settings:
    env = os.getenv("DT_ENV", "")
    url = os.getenv("DT_MCP_URL") or (
        f"https://{env}.apps.dynatrace.com/platform-reserved/mcp-gateway/v0.1/servers/dynatrace-mcp/mcp" if env else "")
    tracing = os.getenv("LANGSMITH_TRACING", "").lower() in ("1", "true", "yes")
    return Settings(
        dt_env=env,
        mcp_url=url,
        dt_token=os.getenv("DT_PLATFORM_TOKEN", ""),
        anthropic_key=os.getenv("ANTHROPIC_API_KEY", ""),
        claude_model=os.getenv("CLAUDE_MODEL", "claude-sonnet-5-5"),
        claude_rca_model=os.getenv("CLAUDE_RCA_MODEL", "claude-opus-5-5"),
        langsmith_key=os.getenv("LANGSMITH_API_KEY", ""),
        langsmith_project=os.getenv("LANGSMITH_PROJECT", "airlab-sre-agent"),
        langsmith_tracing=tracing and bool(os.getenv("LANGSMITH_API_KEY")),
        data_dir=Path(os.getenv("AGENT_DATA_DIR", "./data")),
        cfg=load_yaml_config(config_path),
    )
