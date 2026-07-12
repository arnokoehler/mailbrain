from mailbrain.apply import chunk, desired_labels, group_operations
from mailbrain.planner import PlannedMutation


def test_desired_labels_adds_and_removes_system():
    current = {"INBOX", "UNREAD", "Work"}
    plan = PlannedMutation(
        gmail_id="m1", add_labels=("Reizen",), archive=True, mark_read=True
    )
    assert desired_labels(current, plan) == {"Work", "Reizen"}  # INBOX+UNREAD removed


def test_desired_labels_label_only():
    current = {"INBOX"}
    plan = PlannedMutation(gmail_id="m1", add_labels=("Reizen",), archive=False, mark_read=False)
    assert desired_labels(current, plan) == {"INBOX", "Reizen"}


def test_chunk_splits():
    assert chunk(["1", "2", "3", "4", "5"], 2) == [["1", "2"], ["3", "4"], ["5"]]


def test_chunk_empty():
    assert chunk([], 2) == []


def test_group_operations_groups_identical_ops():
    entries = [
        ("m1", frozenset({"L1"}), frozenset({"INBOX"})),
        ("m2", frozenset({"L1"}), frozenset({"INBOX"})),
        ("m3", frozenset({"L2"}), frozenset()),
    ]
    groups = group_operations(entries)
    assert groups[(frozenset({"L1"}), frozenset({"INBOX"}))] == ["m1", "m2"]
    assert groups[(frozenset({"L2"}), frozenset())] == ["m3"]
