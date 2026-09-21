"""Run a real, target-agnostic OpenCLIP smoke test: python -m app.predictor_test."""

from __future__ import annotations

import logging
import math

from PIL import Image, ImageDraw

from .config import settings
from .labels import LABELS
from .predictor import Predictor


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    predictor = Predictor(LABELS, settings.model_name, settings.model_pretrained, logit_scale=settings.prediction_logit_scale)
    if not predictor.ready or predictor.text_features is None:
        raise RuntimeError(predictor.error or "Predictor did not initialize")
    assert tuple(predictor.text_features.shape)[0] == len(LABELS)
    image = Image.new("RGB", (512, 512), "white")
    draw = ImageDraw.Draw(image)
    draw.ellipse((105, 105, 407, 407), outline="black", width=18)
    draw.line((256, 105, 256, 256), fill="black", width=14)
    draw.line((256, 256, 355, 305), fill="black", width=14)
    scores = predictor.predict_all(image)
    assert len(scores) == len(LABELS)
    assert all(math.isfinite(item.confidence) for item in scores)
    assert math.isclose(sum(item.confidence for item in scores), 1.0, abs_tol=1e-5)
    assert len(scores[:5]) == 5
    print("Predictor self-test passed. Top five:")
    for item in scores[:5]:
        print(f"  {item.label}: {item.confidence:.3f}")


if __name__ == "__main__":
    main()
