from unittest.mock import MagicMock

from sqlalchemy import select

from mailbrain.labels import ensure_label, name_to_id, parent_paths
from mailbrain.storage import db
from mailbrain.storage.models import Label


def test_parent_paths_nested():
    assert parent_paths("A/B/C") == ["A", "A/B", "A/B/C"]


def test_parent_paths_flat():
    assert parent_paths("Reizen") == ["Reizen"]


def test_name_to_id_reads_cache(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    with factory() as s:
        s.add(Label(gmail_id="L1", name="Reizen"))
        s.commit()
    with factory() as s:
        assert name_to_id(s) == {"Reizen": "L1"}


def test_ensure_label_returns_existing(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    client = MagicMock()
    with factory() as s:
        s.add(Label(gmail_id="L1", name="Reizen"))
        s.commit()
        cache = name_to_id(s)
        assert ensure_label(client, s, "Reizen", cache) == "L1"
    client.create_label.assert_not_called()


def test_ensure_label_creates_nested_parents(tmp_path):
    dbp = tmp_path / "state.db"
    db.init_db(dbp)
    factory = db.session_factory(dbp)
    client = MagicMock()
    client.create_label.side_effect = ["id_A", "id_AB"]
    with factory() as s:
        cache: dict[str, str] = {}
        result = ensure_label(client, s, "Beleggen/deGiro", cache)
        s.commit()

    assert result == "id_AB"
    assert [c.args[0] for c in client.create_label.call_args_list] == [
        "Beleggen",
        "Beleggen/deGiro",
    ]
    with factory() as s:
        names = {lbl.name: lbl.gmail_id for lbl in s.scalars(select(Label)).all()}
    assert names == {"Beleggen": "id_A", "Beleggen/deGiro": "id_AB"}
