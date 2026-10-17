"""Embedding encoders for the dense retriever, built from `config/models.yaml` → embeddings.

Every encoder returns float32 ℓ2-normalised vectors, so inner product = cosine (Eq. 4).
Heavy libraries (torch, sentence-transformers, transformers) are imported lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parents[3]
MODELS_YAML = ROOT / "config" / "models.yaml"


@dataclass(frozen=True)
class EmbeddingSpec:
    key: str
    hf_id: str
    backend: str
    max_length: int
    query_instruction: str | None
    doc_prefix: str
    doc_template: str

    def doc_text(self, record: dict) -> str:
        return self.doc_prefix + self.doc_template.format(**record)

    def query_text(self, query: str, instruction: bool) -> str:
        return (self.query_instruction or "") + query if instruction else query


def load_specs(path: Path = MODELS_YAML) -> dict[str, EmbeddingSpec]:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))["embeddings"]
    return {key: EmbeddingSpec(key=key, **c) for key, c in cfg["candidates"].items()}


class Encoder:
    """`precision` is "fp32" or "fp16" (fp16 needs a GPU; used for the Kaggle index)."""

    def __init__(self, spec: EmbeddingSpec, batch_size: int = 32, device: str = "cpu", precision: str = "fp32"):
        import torch

        if precision not in ("fp32", "fp16"):
            raise ValueError(f"unknown precision {precision!r}")
        self.spec = spec
        self.batch_size = batch_size
        self.device = device
        self.precision = precision
        dtype = torch.float16 if precision == "fp16" else torch.float32
        if spec.backend == "sentence_transformers":
            from sentence_transformers import SentenceTransformer

            self._st = SentenceTransformer(spec.hf_id, device=device, model_kwargs={"torch_dtype": dtype})
            self._st.max_seq_length = spec.max_length
            # Renamed in sentence-transformers 6; Kaggle images may ship an older version.
            get_dim = getattr(self._st, "get_embedding_dimension", None) or self._st.get_sentence_embedding_dimension
            self.dim = get_dim()
        elif spec.backend == "byt5_mean":
            from transformers import AutoTokenizer, T5EncoderModel

            self._tok = AutoTokenizer.from_pretrained(spec.hf_id)
            self._enc = T5EncoderModel.from_pretrained(spec.hf_id, torch_dtype=dtype).to(device).eval()
            self.dim = self._enc.config.d_model
        else:
            raise ValueError(f"unknown embedding backend {spec.backend!r}")

    def encode(self, texts: Sequence[str], show_progress: bool = False) -> np.ndarray:
        if self.spec.backend == "sentence_transformers":
            v = self._st.encode(list(texts), batch_size=self.batch_size, normalize_embeddings=False,
                                show_progress_bar=show_progress, convert_to_numpy=True).astype(np.float32)
            # Normalise in fp32 so fp16 runs still give unit vectors.
            return v / np.linalg.norm(v, axis=1, keepdims=True).clip(min=1e-12)
        return self._encode_byt5(texts, show_progress)

    def _encode_byt5(self, texts: Sequence[str], show_progress: bool) -> np.ndarray:
        import torch

        order = np.argsort([len(t) for t in texts], kind="stable")  # length-sorted batches pad less
        out = np.empty((len(texts), self.dim), dtype=np.float32)
        starts = range(0, len(texts), self.batch_size)
        if show_progress:
            from tqdm import tqdm

            starts = tqdm(starts)
        with torch.inference_mode():
            for s in starts:
                idx = order[s:s + self.batch_size]
                x = self._tok([texts[i] for i in idx], padding="longest", truncation=True,
                              max_length=self.spec.max_length, return_tensors="pt").to(self.device)
                h = self._enc(input_ids=x.input_ids, attention_mask=x.attention_mask).last_hidden_state.float()
                m = x.attention_mask.unsqueeze(2)
                v = torch.nn.functional.normalize((h * m).sum(1) / m.sum(1), dim=1)
                out[idx] = v.cpu().numpy()
        return out


def model_revision(hf_id: str) -> str | None:
    """Commit hash of the locally cached snapshot (RNF-2), or None if unknown."""
    try:
        from huggingface_hub import snapshot_download

        return Path(snapshot_download(hf_id, local_files_only=True)).name
    except Exception:  # noqa: BLE001 — metadata only, never fatal
        return None
