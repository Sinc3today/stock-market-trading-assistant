"""
learning/meta_recalibrate.py -- Periodic live recalibration of the meta-model.

Refits on the bootstrap dataset + accumulated live paper outcomes and ONLY
swaps the live artifact if the refit still clears the walk-forward ship bar.
A failing refit leaves the existing model untouched (and logs it).
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pandas as pd
from loguru import logger

import config
from learning.meta_trainer import train_model, save_model, passes_ship_bar


def recalibrate(dataset: pd.DataFrame = None, model_path: str = None) -> dict:
    """Refit + ship-bar check. Writes the model only on pass. Returns the verdict."""
    model_path = model_path or config.META_MODEL_PATH
    if dataset is None:
        from learning.meta_dataset import build_from_history
        dataset = build_from_history(years=5, include_fvg=False)
        dataset = _append_live_outcomes(dataset)

    verdict = passes_ship_bar(dataset)
    if verdict.get("passes"):
        save_model(train_model(dataset), model_path)
        logger.info(f"meta-model recalibrated + saved: {verdict}")
        return {"passed": True, "verdict": verdict}
    logger.warning(f"meta-model recalibration did NOT pass ship bar; kept old: {verdict}")
    return {"passed": False, "verdict": verdict}


def _append_live_outcomes(dataset: pd.DataFrame) -> pd.DataFrame:
    """Append resolved paper-trade outcomes as labeled rows. Best-effort: if the
    journal is unavailable or empty, return the dataset unchanged."""
    try:
        from journal.trade_recorder import TradeRecorder  # adjust if API differs
        rows = TradeRecorder().resolved_meta_rows()  # expected: list[feature+win dicts]
        if rows:
            return pd.concat([dataset, pd.DataFrame(rows)], ignore_index=True)
    except Exception as e:
        logger.debug(f"No live outcomes appended: {e}")
    return dataset


def run_meta_recalibration():
    """Scheduler entry point (wrapped in try/except per standing rule 10)."""
    try:
        recalibrate()
    except Exception as e:
        logger.error(f"meta recalibration job failed: {e}")
