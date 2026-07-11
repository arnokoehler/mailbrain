from pathlib import Path

from mailbrain import config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_default_settings_load():
    s = config.load_settings(CONFIG_DIR / "settings.yaml")
    assert s.ai.provider == "mistral"
    assert s.ai.enabled is False  # Phase 1 = no AI


def test_default_rules_load_and_are_nonempty():
    rf = config.load_rules(CONFIG_DIR / "rules.yaml")
    ids = {r.id for r in rf.rules}
    assert "booking-payment" in ids
    assert "password-reset" in ids
    for r in rf.rules:
        assert r.actions.add_labels or r.actions.archive or r.actions.mark_read


def test_no_rule_requests_deletion():
    # Design v0.2: the tool never deletes. Rules only label/archive/mark_read.
    rf = config.load_rules(CONFIG_DIR / "rules.yaml")
    for r in rf.rules:
        assert not hasattr(r.actions, "trash")
        assert not hasattr(r.actions, "delete")
