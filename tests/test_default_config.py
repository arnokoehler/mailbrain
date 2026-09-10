from pathlib import Path

import pytest
from pydantic import ValidationError

from mailbrain import config

CONFIG_DIR = Path(__file__).resolve().parents[1] / "config"


def test_default_settings_load():
    s = config.load_settings(CONFIG_DIR / "settings.yaml")
    assert s.ai.provider == "mistral"
    assert s.ai.enabled is False  # Phase 1 = no AI


def test_default_rules_load_and_are_nonempty():
    rf = config.load_rules(CONFIG_DIR / "rules.yaml")
    assert rf.rules, "expected at least one rule"
    ids = [r.id for r in rf.rules]
    assert len(ids) == len(set(ids)), "rule ids must be unique"
    for r in rf.rules:
        assert r.actions.add_labels or r.actions.archive or r.actions.mark_read


def test_rule_actions_reject_unknown_action(tmp_path):
    # Design v0.2: the tool never deletes. Unknown actions like `trash` must be
    # REJECTED (not silently dropped) so a delete action can never sneak in.
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: sneaky\n"
        "    match:\n"
        "      from_domain: [x.com]\n"
        "    actions:\n"
        "      trash: true\n"
    )
    with pytest.raises(ValidationError):
        config.load_rules(p)
