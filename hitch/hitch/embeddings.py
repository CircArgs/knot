"""sentence-transformers wrapper, with a lazy global model + simple cache.

The model loads on first encode call so worker startup isn't blocked
waiting for ~80MB of weights to materialize.
"""

from __future__ import annotations

import threading

_model = None
_model_lock = threading.Lock()


def _get_model(name: str):
    global _model
    if _model is not None:
        return _model
    with _model_lock:
        if _model is None:
            from sentence_transformers import SentenceTransformer

            _model = SentenceTransformer(name)
    return _model


def embed(texts: list[str], *, model_name: str) -> list[list[float]]:
    """Return one float-list per input text. List-of-list is what
    knot.update_slot_sql's jsonb param expects (json-serializable)."""
    if not texts:
        return []
    model = _get_model(model_name)
    vectors = model.encode(
        texts,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return vectors.tolist()
