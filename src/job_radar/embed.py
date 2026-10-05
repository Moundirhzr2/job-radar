"""Local models (fastembed, ONNX Runtime on CPU): embeddings and re-ranking, no API key.

Models are downloaded once into data/models as plain files. The Hugging Face cache stores
large weights as symlinks into a shared blob folder, which recent ONNX Runtime versions refuse
to load ("external data path escapes model directory"); a plain copy avoids that.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

import numpy as np

from .models import Offer

DEFAULT_MODEL = "intfloat/multilingual-e5-large"  # 1024 dimensions, French and English
MODELS_DIR = Path(os.environ.get("RADAR_MODELS_DIR", "data/models"))
KIND_WORDS = {
    "internship": "stage",
    "apprenticeship": "alternance",
    "student_job": "job étudiant temps partiel",
    "job": "emploi",
}
# The vector summarises an offer (title + start of the description); full-text search still
# covers the whole description and the re-ranker rereads the full offer. 600 characters
# index 3,255 offers in about 16 minutes on 4 CPUs, against 39 minutes for 1,500.
MAX_DESCRIPTION = 600


def offer_text(title: str, company: str, city: str, kinds: Sequence[str], description: str) -> str:
    """What gets embedded: the fields a student would judge an offer on, title first."""
    kind_words = ", ".join(KIND_WORDS.get(k, k) for k in kinds)
    head = " — ".join(p for p in (title, company, city) if p)
    return f"{head}\n{kind_words}\n{description[:MAX_DESCRIPTION]}".strip()


def offer_text_of(offer: Offer) -> str:
    return offer_text(
        offer.title, offer.company, offer.location.city, sorted(offer.kinds), offer.description
    )


def local_model_dir(description: dict) -> Path:
    """Download a fastembed model's files from Hugging Face into a plain folder (once)."""
    from huggingface_hub import snapshot_download

    repo = description["sources"]["hf"]
    target = MODELS_DIR / repo.replace("/", "__")
    files = [description["model_file"], *description.get("additional_files", [])]
    if not all((target / f).exists() for f in files):
        snapshot_download(
            repo,
            local_dir=target,
            allow_patterns=[*files, "*.json", "*.txt", "*.model"],
        )
    return target


class Embedder:
    def __init__(self, model: str = DEFAULT_MODEL, batch_size: int = 16):
        from fastembed import TextEmbedding

        (description,) = [m for m in TextEmbedding.list_supported_models() if m["model"] == model]
        self.model_name = model
        self.dim: int = description["dim"]
        self.batch_size = batch_size
        self._model = TextEmbedding(model, specific_model_path=str(local_model_dir(description)))
        # E5 models are trained with these prefixes; without them retrieval is worse.
        e5 = "e5" in model.lower()
        self._query, self._passage = ("query: ", "passage: ") if e5 else ("", "")

    def passages(self, texts: Sequence[str]) -> list[np.ndarray]:
        return list(
            self._model.embed([self._passage + t for t in texts], batch_size=self.batch_size)
        )

    def query(self, text: str) -> np.ndarray:
        return next(iter(self._model.embed([self._query + text])))
