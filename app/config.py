from __future__ import annotations

import os
from dataclasses import dataclass


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    return default if value is None else value.lower() not in {"0", "false", "no", "off"}


@dataclass(frozen=True)
class Settings:
    game_duration_seconds: int = int(os.getenv("GAME_DURATION_SECONDS", "30"))
    win_confidence_threshold: float = float(os.getenv("WIN_CONFIDENCE_THRESHOLD", "0.55"))
    prediction_interval_seconds: float = float(os.getenv("PREDICTION_INTERVAL_SECONDS", "0.65"))
    prediction_logit_scale: float = float(os.getenv("PREDICTION_LOGIT_SCALE", "25"))
    public_host: str | None = os.getenv("PUBLIC_HOST")
    port: int = int(os.getenv("PORT", "8000"))
    model_name: str = os.getenv("OPENCLIP_MODEL", "ViT-B-32")
    model_pretrained: str = os.getenv("OPENCLIP_PRETRAINED", "laion2b_s34b_b79k")
    predictor_enabled: bool = _bool_env("PREDICTOR_ENABLED", True)
    save_debug_prediction_images: bool = _bool_env("SAVE_DEBUG_PREDICTION_IMAGES", False)


settings = Settings()
