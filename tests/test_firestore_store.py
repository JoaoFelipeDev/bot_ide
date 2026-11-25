"""Testes do store Firestore com client mockado (sem rede)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from id_monitor.cloud import firestore_store as store
from id_monitor.cloud.settings import CloudSettings


class FakeDoc:
    def __init__(self, data=None, exists=False, doc_id="x"):
        self._data = data or {}
        self.exists = exists
        self.id = doc_id
        self.reference = self

    def get(self):
        return self

    def to_dict(self):
        return dict(self._data)

    def set(self, data, merge=False):
        if merge:
            self._data.update(data)
        else:
            self._data = dict(data)
        self.exists = True

    def delete(self):
        self.exists = False
        self._data = {}


class FakeCollection:
    def __init__(self):
        self.docs: dict[str, FakeDoc] = {}

    def document(self, doc_id: str):
        if doc_id not in self.docs:
            self.docs[doc_id] = FakeDoc(doc_id=doc_id)
        return self.docs[doc_id]

    def stream(self):
        return [d for d in self.docs.values() if d.exists]

    def collection(self, name: str):
        # subcollection no FakeDoc — usamos dict no parent
        key = f"_sub_{name}"
        if not hasattr(self, key):
            setattr(self, key, FakeCollection())
        return getattr(self, key)


def test_book_doc_id_prefers_isbn():
    assert store.book_doc_id(SimpleNamespace(isbn="9788573414936", id=9)) == "isbn_9788573414936"
    assert store.book_doc_id(SimpleNamespace(isbn=None, id=9)) == "local_9"


def test_get_and_save_monitor_settings(monkeypatch):
    root = FakeCollection()
    settings = CloudSettings(enabled=True, project_id="ide-digital", service_account_json="x.json")

    monkeypatch.setattr(store, "cloud_configured", lambda: True)
    monkeypatch.setattr(store, "load_cloud_settings", lambda: settings)
    monkeypatch.setattr(store, "_col", lambda _s=None: root)

    # data doc subcollections
    data_doc = root.document("data")

    def data_doc_fn(_s=None):
        return data_doc

    # FakeDoc needs .collection
    def collection(name):
        if not hasattr(data_doc, "_subs"):
            data_doc._subs = {}
        if name not in data_doc._subs:
            data_doc._subs[name] = FakeCollection()
        return data_doc._subs[name]

    data_doc.collection = collection  # type: ignore[method-assign]
    monkeypatch.setattr(store, "_data_doc", data_doc_fn)

    store.save_monitor_settings({
        "schedule": {"enabled": True, "weekdays": ["mon", "fri"], "time": "10:00"},
        "email": {"from_email": "a@b.com", "recipients": ["c@d.com"]},
    })
    saved = root.document("settings").to_dict()
    assert saved["schedule"]["weekdays"] == ["mon", "fri"]
    loaded = store.get_monitor_settings(settings)
    assert loaded["schedule"]["time"] == "10:00"
    assert loaded["email"]["recipients"] == ["c@d.com"]


def test_upsert_book_writes_subcollection(monkeypatch):
    settings = CloudSettings(enabled=True, project_id="ide-digital", service_account_json="x.json")
    monkeypatch.setattr(store, "cloud_configured", lambda: True)
    monkeypatch.setattr(store, "load_cloud_settings", lambda: settings)

    books = FakeCollection()
    data = FakeDoc(exists=True, doc_id="data")
    data.collection = lambda name: books  # type: ignore[method-assign]
    monkeypatch.setattr(store, "_data_doc", lambda _s=None: data)

    product = SimpleNamespace(
        id=1, title="A Genese", author="Kardec", isbn="9788573414936",
        sku=None, price_id_livraria=20.0, origin=SimpleNamespace(value="manual"),
        active=True, created_at=None,
    )
    store.upsert_book(product)
    doc = books.document("isbn_9788573414936")
    assert doc.exists
    assert doc.to_dict()["title"] == "A Genese"
