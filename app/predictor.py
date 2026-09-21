from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from PIL import Image

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Prediction:
    label: str
    confidence: float


class Predictor:
    """OpenCLIP zero-shot sketch classifier with cached label embeddings."""

    PROMPTS = (
        "a simple black and white drawing of a {}",
        "a hand drawn sketch of a {}",
        "a simple doodle of a {}",
    )

    def __init__(self, labels: list[str], model_name: str, pretrained: str, enabled: bool = True):
        self.labels = labels
        self.model_name = model_name
        self.pretrained = pretrained
        self.enabled = enabled
        self.ready = False
        self.error: str | None = None
        self.device = "cpu"
        self._lock = threading.Lock()
        if enabled:
            self._load()
        else:
            self.error = "Predictor disabled by PREDICTOR_ENABLED"

    def _load(self) -> None:
        try:
            import open_clip
            import torch

            self.torch = torch
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            logger.info("Loading OpenCLIP %s (%s) on %s", self.model_name, self.pretrained, self.device)
            model, _, preprocess = open_clip.create_model_and_transforms(
                self.model_name, pretrained=self.pretrained, device=self.device
            )
            tokenizer = open_clip.get_tokenizer(self.model_name)
            model.eval()
            prompts = [template.format(label) for label in self.labels for template in self.PROMPTS]
            with torch.inference_mode():
                tokens = tokenizer(prompts).to(self.device)
                vectors = model.encode_text(tokens)
                vectors = vectors / vectors.norm(dim=-1, keepdim=True)
                vectors = vectors.reshape(len(self.labels), len(self.PROMPTS), -1).mean(dim=1)
                self.text_features = vectors / vectors.norm(dim=-1, keepdim=True)
            self.model = model
            self.preprocess = preprocess
            self.ready = True
            logger.info("OpenCLIP predictor ready with %d labels", len(self.labels))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.exception("Predictor unavailable; drawing sync will continue")

    @property
    def status(self) -> dict:
        return {"ready": self.ready, "device": self.device, "error": self.error}

    def predict(self, image: Image.Image, top_k: int = 5) -> list[Prediction]:
        if not self.ready:
            return []
        torch = self.torch
        with self._lock, torch.inference_mode():
            tensor = self.preprocess(image.convert("RGB")).unsqueeze(0).to(self.device)
            image_features = self.model.encode_image(tensor)
            image_features = image_features / image_features.norm(dim=-1, keepdim=True)
            # Softmax is relative confidence among configured labels, not a calibrated probability.
            logits = 100.0 * image_features @ self.text_features.T
            scores = logits.softmax(dim=-1)[0]
            values, indices = scores.topk(min(top_k, len(self.labels)))
            return [
                Prediction(self.labels[index], float(value))
                for value, index in zip(values.cpu().tolist(), indices.cpu().tolist())
            ]

