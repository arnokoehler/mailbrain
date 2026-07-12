from mailbrain import config


def test_settings_defaults_from_empty_yaml(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("")
    s = config.load_settings(p)
    assert s.gmail.page_size == 500
    assert s.gmail.batch_size == 1000
    assert s.ai.enabled is False
    assert s.ai.provider == "mistral"
    assert s.ai.confidence_floor == 0.85
    assert s.notion.enabled is False


def test_settings_override(tmp_path):
    p = tmp_path / "settings.yaml"
    p.write_text("gmail:\n  page_size: 100\nai:\n  confidence_floor: 0.9\n")
    s = config.load_settings(p)
    assert s.gmail.page_size == 100
    assert s.ai.confidence_floor == 0.9
    assert s.gmail.batch_size == 1000


def test_load_rules(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: booking-payment\n"
        "    match:\n"
        "      from_domain: [booking.com]\n"
        "      subject_contains: [receipt]\n"
        "    actions:\n"
        "      add_labels: [Reizen]\n"
        "      archive: true\n"
    )
    rf = config.load_rules(p)
    assert len(rf.rules) == 1
    rule = rf.rules[0]
    assert rule.id == "booking-payment"
    assert rule.match.from_domain == ["booking.com"]
    assert rule.match.subject_contains == ["receipt"]
    assert rule.actions.add_labels == ["Reizen"]
    assert rule.actions.archive is True
    assert rule.actions.mark_read is False


def test_rule_match_defaults_empty(tmp_path):
    p = tmp_path / "rules.yaml"
    p.write_text(
        "rules:\n"
        "  - id: pw-reset\n"
        "    match:\n"
        "      subject_regex: ['(?i)password reset']\n"
        "      older_than_days: 3\n"
        "    actions:\n"
        "      add_labels: [to-be-removed]\n"
    )
    rule = config.load_rules(p).rules[0]
    assert rule.match.subject_regex == ["(?i)password reset"]
    assert rule.match.older_than_days == 3
    assert rule.match.from_domain == []
    assert rule.actions.archive is False


def test_load_settings_missing_file_returns_defaults(tmp_path):
    s = config.load_settings(tmp_path / "does_not_exist.yaml")
    assert s.ai.provider == "mistral"


def test_load_rules_missing_file_returns_empty(tmp_path):
    rf = config.load_rules(tmp_path / "nope.yaml")
    assert rf.rules == []
