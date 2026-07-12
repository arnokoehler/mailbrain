import pytest
from pydantic import ValidationError

from mailbrain import config


def test_invalid_regex_rejected(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: bad\n"
        "    match:\n"
        "      subject_regex: ['[unterminated']\n"
        "    actions:\n"
        "      add_labels: [X]\n"
    )
    with pytest.raises(ValidationError):
        config.load_rules(p)


def test_valid_regex_accepted(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: good\n"
        "    match:\n"
        "      subject_regex: ['(?i)password reset']\n"
        "    actions:\n"
        "      add_labels: [X]\n"
    )
    rf = config.load_rules(p)
    assert rf.rules[0].match.subject_regex == ["(?i)password reset"]
