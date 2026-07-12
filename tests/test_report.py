from mailbrain.planner import PlannedMutation
from mailbrain.report import Metrics, summarize


def test_summarize_counts():
    plans = [
        PlannedMutation(gmail_id="m1", add_labels=("Reizen",), archive=True, mark_read=False),
        PlannedMutation(gmail_id="m2", add_labels=("Beleggen",), archive=False, mark_read=True),
        PlannedMutation(gmail_id="m3", add_labels=(), archive=True, mark_read=False),
    ]
    metrics = summarize(plans, scanned=10)
    assert metrics == Metrics(scanned=10, planned=3, labeled=2, archived=2, marked_read=1)


def test_summarize_empty():
    assert summarize([], scanned=5) == Metrics(
        scanned=5, planned=0, labeled=0, archived=0, marked_read=0
    )
