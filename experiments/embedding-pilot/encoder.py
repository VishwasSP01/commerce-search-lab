"""Local-only E5 encoding with the model-card pooling contract."""

import os
import unicodedata

os.environ["PYTORCH_ENABLE_MPS_FALLBACK"] = "0"
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"

import numpy as np
import torch
from transformers import AutoModel, AutoTokenizer


def prepare_texts(texts: list[str], kind: str) -> list[str]:
    if kind not in ("passage", "query"):
        raise ValueError("Expected passage or query input")
    return [kind + ": " + unicodedata.normalize("NFKC", text) for text in texts]


def masked_mean(hidden, mask):
    counts = mask.sum(dim=1)
    if torch.any(counts == 0).item():
        raise ValueError("Cannot pool an entirely masked sequence")
    return hidden.masked_fill(~mask[..., None].bool(), 0.0).sum(dim=1) / counts[..., None]


class E5Encoder:
    def __init__(self, model_dir, policy: dict, device: str | None = None):
        self.policy = policy
        self.device = device or ("mps" if torch.backends.mps.is_available() else "cpu")
        if self.device not in ("mps", "cpu"):
            raise ValueError("Pilot supports explicit CPU or MPS only")
        if self.device == "mps" and not torch.backends.mps.is_available():
            raise ValueError("MPS requested but unavailable")
        torch.set_num_threads(policy["cpu_threads"])
        self.tokenizer = AutoTokenizer.from_pretrained(model_dir, local_files_only=True,
                                                       trust_remote_code=False, use_fast=True)
        self.model = AutoModel.from_pretrained(model_dir, local_files_only=True, trust_remote_code=False,
                                               use_safetensors=True, dtype=torch.float32,
                                               attn_implementation="eager").eval().to(self.device)
        if self.model.config.hidden_size != policy["embedding_dimensions"]:
            raise ValueError("Model dimension differs from experiment")
        self.observed_mps_tensor_bytes = self.observed_mps_driver_bytes = 0

    def lengths(self, texts: list[str], kind: str = "passage") -> list[int]:
        return self.tokenizer(prepare_texts(texts, kind), padding=False, truncation=False,
                              return_length=True, verbose=False)["length"]

    def encode(self, texts: list[str], kind: str = "passage") -> np.ndarray:
        if not texts:
            raise ValueError("Empty embedding batch")
        inputs = self.tokenizer(prepare_texts(texts, kind), max_length=self.policy["max_length"],
                                padding=True, truncation=True, return_tensors="pt")
        inputs = {key: value.to(self.device) for key, value in inputs.items()}
        with torch.inference_mode():
            hidden = self.model(**inputs).last_hidden_state
            vectors = torch.nn.functional.normalize(masked_mean(hidden, inputs["attention_mask"]), p=2, dim=1)
            if self.device == "mps":
                torch.mps.synchronize()
                self.observed_mps_tensor_bytes = max(self.observed_mps_tensor_bytes, torch.mps.current_allocated_memory())
                self.observed_mps_driver_bytes = max(self.observed_mps_driver_bytes, torch.mps.driver_allocated_memory())
            return vectors.cpu().numpy().astype("<f4", copy=False)
