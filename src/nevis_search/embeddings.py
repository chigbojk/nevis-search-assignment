from typing import Protocol

import numpy as np
from model2vec import StaticModel
from numpy.typing import NDArray

VectorBatch = NDArray[np.float32]


class Encoder(Protocol):
    def encode(self, texts: list[str]) -> VectorBatch: ...


class LocalEncoder:
    def __init__(self, model_path: str):
        self._model = StaticModel.from_pretrained(model_path, normalize=True)

    def encode(self, texts: list[str]) -> VectorBatch:
        # Chunking bounds input size. Disable internal truncation so unusual tokenization
        # (e.g. account numbers) cannot silently discard the end of a chunk.
        return np.asarray(self._model.encode(texts, max_length=None), dtype=np.float32)
