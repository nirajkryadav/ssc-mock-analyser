"""Storing a mock's original PDFs alongside its record."""
import records


def _pdfs():
    return [("PART A.pdf", b"%PDF-a" * 10), ("part-b.pdf", b"%PDF-b")]


def test_memory_store_keeps_and_deletes_files():
    s = records.MemoryStore()
    s.put("m", {"id": "m"})
    s.put_files("m", _pdfs())
    assert s.get_files("m") == _pdfs()
    s.delete("m")
    assert s.get_files("m") == []


def test_file_store_keeps_files_across_restarts(tmp_path):
    path = tmp_path / "records.json"
    s = records.FileStore(path)
    s.put("m", {"id": "m"})
    s.put_files("m", _pdfs())
    again = records.FileStore(path)
    assert [b for _, b in again.get_files("m")] == [b for _, b in _pdfs()]
    assert [n for n, _ in again.get_files("m")] == ["PART_A.pdf", "part-b.pdf"]


def test_file_store_replacing_files_drops_the_old_ones(tmp_path):
    s = records.FileStore(tmp_path / "records.json")
    s.put_files("m", _pdfs())
    s.put_files("m", [("new.pdf", b"x")])
    assert s.get_files("m") == [("new.pdf", b"x")]
    s.put("m", {"id": "m"})
    s.delete("m")
    assert s.get_files("m") == [] and not (tmp_path / "files" / "m").exists()


def test_file_names_cannot_escape_the_store(tmp_path):
    s = records.FileStore(tmp_path / "records.json")
    s.put_files("m", [("../../etc/passwd", b"x")])
    assert all(p.is_relative_to(tmp_path) for p in tmp_path.rglob("*"))
    assert s.get_files("m")[0][0] == "passwd"


# ---------------------------------------------------------- Firestore, faked


class _Snap:
    def __init__(self, ref, data):
        self.reference, self._data, self.exists = ref, data, data is not None

    def to_dict(self):
        return dict(self._data)


class _Doc:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def set(self, data):
        self.db[self.path] = dict(data)

    def get(self):
        return _Snap(self, self.db.get(self.path))

    def delete(self):
        self.db.pop(self.path, None)

    def collection(self, name):
        return _Col(self.db, f"{self.path}/{name}")


class _Col:
    def __init__(self, db, path):
        self.db, self.path = db, path

    def document(self, did):
        return _Doc(self.db, f"{self.path}/{did}")

    def stream(self):
        depth = self.path.count("/") + 1
        return [_Snap(_Doc(self.db, p), d) for p, d in list(self.db.items())
                if p.startswith(self.path + "/") and p.count("/") == depth]


class _Batch:
    def __init__(self):
        self.ops = []

    def delete(self, ref):
        self.ops.append(ref)

    def commit(self):
        for r in self.ops:
            r.delete()


def _fake_firestore():
    db = {}
    s = records.FirestoreStore.__new__(records.FirestoreStore)
    s._col = _Col(db, "mocks")
    s._client = type("C", (), {"batch": staticmethod(_Batch)})()
    return s, db


def test_firestore_splits_big_pdfs_under_the_document_limit(monkeypatch):
    monkeypatch.setattr(records, "CHUNK", 1000)
    s, db = _fake_firestore()
    big = bytes(range(256)) * 10  # 2560 bytes -> 3 chunks
    s.put("m", {"id": "m"})
    s.put_files("m", [("a.pdf", big), ("b.pdf", b"small")])
    chunks = [p for p in db if p.startswith("mocks/m/files/")]
    assert len(chunks) == 4
    assert all(len(db[p]["data"]) <= 1000 for p in chunks)
    assert s.get_files("m") == [("a.pdf", big), ("b.pdf", b"small")]
    assert [r["id"] for r in s.list()] == ["m"]  # chunks are not listed as mocks


def test_firestore_delete_removes_the_pdf_chunks():
    s, db = _fake_firestore()
    s.put("m", {"id": "m"})
    s.put_files("m", _pdfs())
    assert s.delete("m")
    assert db == {}
