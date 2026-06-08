"""Inference server for the fine-tuned VLM game-playing model.

Exports
-------
GameAgentInference
    Loads a local VLM + LoRA adapter for single-frame game-action prediction.
app
    FastAPI application instance (:func:`predict_endpoint`, :func:`health_endpoint`).
"""

from __future__ import annotations

from .server import GameAgentInference, app

__all__ = ["GameAgentInference", "app"]
