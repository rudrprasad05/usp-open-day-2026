from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.hf_auth import configure_hf_token


class HuggingFaceAuthTests(unittest.TestCase):
    def test_bare_token_is_available_to_hugging_face_hub(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("hf_example_token\n", encoding="utf-8")
            with patch.dict(os.environ, {"HF_TOKEN": ""}):
                self.assertEqual(configure_hf_token(env_file), ".env")
                from huggingface_hub import get_token

                self.assertEqual(get_token(), "hf_example_token")

    def test_assignment_and_environment_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text("# local secret\nHF_TOKEN='hf_file_token'\n", encoding="utf-8")
            with patch.dict(os.environ, {"HF_TOKEN": ""}):
                self.assertEqual(configure_hf_token(env_file), ".env")
                self.assertEqual(os.environ["HF_TOKEN"], "hf_file_token")
            with patch.dict(os.environ, {"HF_TOKEN": "hf_process_token"}):
                self.assertEqual(configure_hf_token(env_file), "environment")
                self.assertEqual(os.environ["HF_TOKEN"], "hf_process_token")

    def test_missing_or_unrelated_file_does_not_set_token(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            with patch.dict(os.environ, {"HF_TOKEN": ""}):
                self.assertEqual(configure_hf_token(env_file), "none")
                env_file.write_text("PORT=8888\n", encoding="utf-8")
                self.assertEqual(configure_hf_token(env_file), "none")
                self.assertEqual(os.environ["HF_TOKEN"], "")
