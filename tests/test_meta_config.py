import os, sys
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import config


def test_meta_flags_exist_with_safe_defaults():
    assert config.META_LABEL_ENABLED is False          # inert until proven
    assert 0.0 < config.META_PROB_THRESHOLD < 1.0
    assert config.META_TIER_CUTOFFS["med"] <= config.META_TIER_CUTOFFS["high"]
    assert config.META_MODEL_PATH.endswith(".joblib")
