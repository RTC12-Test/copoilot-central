import os
import yaml
from typing import Dict, Optional

from .base import AIModel
from .copilot_model import CopilotCLIModel
from .provider_models import OpenAIModel, AnthropicModel

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Resolution: $CI_MODEL_CONFIG > <project>/config/ai_models.yaml.
DEFAULT_CONFIG_PATH = (os.environ.get("CI_MODEL_CONFIG")
                       or os.path.join(_PROJECT_ROOT, "config", "ai_models.yaml"))


def load_model_config(path: str = DEFAULT_CONFIG_PATH) -> Dict:
    if os.path.exists(path):
        with open(path) as f:
            return yaml.safe_load(f) or {}
    return {}


def _env_or(cfg: Optional[str], default: str) -> str:
    if cfg:
        return cfg
    return default


def get_ai_model(config: Optional[Dict] = None) -> AIModel:
    """Build the configured AIModel (plug-and-play).

    Selection order:
      1. config['ai']['model'] (e.g. copilot | openai | anthropic)
      2. $CI_AI_MODEL env var
      3. default: copilot
    """
    cfg = config or load_model_config()
    model_cfg = (cfg.get("ai") or {}).get("model", {}) or {}
    # Env var CI_AI_MODEL takes precedence over config `name`.
    name = (os.environ.get("CI_AI_MODEL") or model_cfg.get("name") or "copilot").lower()

    if name == "openai":
        return OpenAIModel(
            api_key=model_cfg.get("api_key") or os.environ.get("OPENAI_API_KEY"),
            model=_env_or(model_cfg.get("model"), "gpt-4o"),
            timeout=int(model_cfg.get("timeout", 120)),
        )
    if name == "anthropic":
        return AnthropicModel(
            api_key=model_cfg.get("api_key") or os.environ.get("ANTHROPIC_API_KEY"),
            model=_env_or(model_cfg.get("model"), "claude-sonnet-4-5"),
            timeout=int(model_cfg.get("timeout", 120)),
        )
    # default: copilot CLI
    return CopilotCLIModel(
        binary=_env_or(model_cfg.get("binary"), "copilot"),
        timeout=int(model_cfg.get("timeout", 180)),
    )


__all__ = ["AIModel", "CopilotCLIModel", "OpenAIModel", "AnthropicModel",
           "get_ai_model", "load_model_config"]
