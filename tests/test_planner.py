from mailbrain.planner import plan_mutations
from mailbrain.rules.models import Classification


def _c(gmail_id="m1", add_labels=("Reizen",), archive=False, mark_read=False):
    return Classification(
        gmail_id=gmail_id,
        matched_rule_ids=("r",),
        add_labels=add_labels,
        archive=archive,
        mark_read=mark_read,
    )


def test_adds_only_missing_labels():
    current = {"m1": {"Reizen", "INBOX"}}
    plans = plan_mutations([_c(add_labels=("Reizen", "Administratie"))], current)
    assert len(plans) == 1
    assert plans[0].add_labels == ("Administratie",)


def test_archive_only_when_in_inbox():
    current = {"m1": {"INBOX"}}
    plans = plan_mutations([_c(add_labels=(), archive=True)], current)
    assert plans[0].archive is True

    current2 = {"m1": set()}
    plans2 = plan_mutations([_c(add_labels=(), archive=True)], current2)
    assert plans2 == []


def test_mark_read_only_when_unread():
    current = {"m1": {"UNREAD"}}
    plans = plan_mutations([_c(add_labels=(), mark_read=True)], current)
    assert plans[0].mark_read is True

    current2 = {"m1": set()}
    plans2 = plan_mutations([_c(add_labels=(), mark_read=True)], current2)
    assert plans2 == []


def test_noop_when_already_in_desired_state():
    current = {"m1": {"Reizen"}}
    plans = plan_mutations([_c(add_labels=("Reizen",))], current)
    assert plans == []


def test_missing_current_state_treats_as_empty():
    plans = plan_mutations([_c(add_labels=("Reizen",))], {})
    assert plans[0].add_labels == ("Reizen",)
