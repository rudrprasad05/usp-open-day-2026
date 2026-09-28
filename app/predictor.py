from __future__ import annotations

import logging
import math
import threading
from dataclasses import dataclass

from PIL import Image

from .hf_auth import configure_hf_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float


class Predictor:
    """OpenCLIP zero-shot sketch classifier with cached label embeddings."""

    PROMPTS = (
        "a simple drawing of a {}",
        "a black and white sketch of a {}",
        "a hand drawn doodle of a {}",
        "an icon-like drawing of a {}",
    )

    def __init__(self, labels: list[str], model_name: str, pretrained: str, enabled: bool = True, logit_scale: float = 25.0):
        self.labels = labels
        self.model_name = model_name
        self.pretrained = pretrained
        self.enabled = enabled
        self.logit_scale = logit_scale
        self.ready = False
        self.error: str | None = None
        self.device = "cpu"
        self.text_features = None
        self._lock = threading.Lock()
        if enabled:
            self._load()
        else:
            self.error = "Predictor disabled by PREDICTOR_ENABLED"

    def _load(self) -> None:
        logger.info("[AI] Loading predictor...")
        logger.info("[AI] Model: %s (%s)", self.model_name, self.pretrained)
        try:
            token_source = configure_hf_token()
            if token_source != "none":
                logger.info("[AI] Hugging Face authentication configured from %s", token_source)
            import open_clip
            import torch

            self.torch = torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("[AI] Device: %s", self.device)
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name, pretrained=self.pretrained, device=self.device
            )
            tokenizer = open_clip.get_tokenizer(self.model_name)
            model.eval()
            logger.info("[AI] Loaded %d labels", len(self.labels))
            prompts = [template.format(label) for label in self.labels for template in self.PROMPTS]
            with torch.inference_mode():
                tokens = tokenizer(prompts).to(self.device)
                vectors = model.encode_text(tokens)
                vectors = vectors / vectors.norm(dim=-1, keepdim=True)
                vectors = vectors.reshape(len(self.labels), len(self.PROMPTS), -1).mean(dim=1)
                self.text_features = vectors / vectors.norm(dim=-1, keepdim=True)
            if self.text_features.ndim != 2 or self.text_features.shape[0] != len(self.labels):
                raise ValueError(f"Unexpected text embedding shape: {tuple(self.text_features.shape)}")
            if not torch.isfinite(self.text_features).all().item():
                raise ValueError("Text embeddings contain non-finite values")
            logger.info("[AI] Text embeddings generated: shape=%s", tuple(self.text_features.shape))
            self.model = model
            self.preprocess = preprocess
            self.ready = True
            logger.info("[AI] Predictor ready")
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("[AI ERROR] Predictor initialization failed; drawing sync will continue")

    @property
    def status(self) -> dict:
        return {"ready": self.ready, "device": self.device, "error": self.error}

    def predict_all(self, image: Image.Image) -> list[Prediction]:
        if not self.ready:
            raise RuntimeError(self.error or "Predictor is not ready")
        torch = self.torch
        with self._lock, torch.inference_mode():
            tensor = self.preprocess(image.convert("RGB")).unsqueeze(0).to(self.device)
            if not torch.isfinite(tensor).all().item():
                raise ValueError("Preprocessed image contains non-finite values")
            image_features = self.model.encode_image(tensor)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            if not torch.isfinite(image_features).all().item():
                raise ValueError("Image embedding contains non-finite values")
            # A moderate scale keeps early guesses visible instead of making
            # almost every top score round to 100%. This is relative confidence
            # among configured labels, not a calibrated statistical probability.
            logits = self.logit_scale * image_features @ self.text_features.T
            scores = logits.softmax(dim=-1)[0]
            if scores.numel() != len(self.labels) or not torch.isfinite(scores).all().item():
                raise ValueError("Class scores are missing or non-finite")
            results = [Prediction(label, float(value)) for label, value in zip(self.labels, scores.cpu().tolist())]
            if not all(math.isfinite(p.confidence) for p in results):
                raise ValueError("Class confidence is non-finite")
            return sorted(results, key=lambda p: p.confidence, reverse=True)

    def predict(self, image: Image.Image, top_k: int = 5) -> list[Prediction]:
        return self.predict_all(image)[:top_k]
