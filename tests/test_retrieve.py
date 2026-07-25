"""检索节点单测（规格 3 / 4.3）。

短路机制：retrieval_score < THRESHOLD → short_circuited=True（无依据不自动出站）。
THRESHOLD 已设为 0.60；测试中用 monkeypatch 覆盖以验证各种阈值行为。"""
from app.graph.nodes import retrieve


def _fake_session():
    class _S:
        def close(self):
            pass

    return _S()


def _patch(monkeypatch, chunks):
    monkeypatch.setattr(retrieve, "SessionLocal", lambda: _fake_session())
    monkeypatch.setattr(retrieve, "search", lambda db, intent, query, k: chunks)


def test_threshold_none_always_short_circuits(monkeypatch):
    # THRESHOLD=None：即使检索到高相关片段也保守短路
    monkeypatch.setattr(retrieve, "THRESHOLD", None)
    _patch(monkeypatch, [{"chunk_id": 1, "content": "c", "score": 0.99, "source_url": "u"}])
    out = retrieve.retrieve({"intent": "return", "raw_text": "x", "masked_text": "x"})
    assert out["short_circuited"] is True
    assert out["retrieval_score"] == 0.99


def test_threshold_below_top_passes(monkeypatch):
    monkeypatch.setattr(retrieve, "THRESHOLD", 0.5)
    _patch(monkeypatch, [{"chunk_id": 1, "content": "c", "score": 0.9, "source_url": "u"}])
    out = retrieve.retrieve({"intent": "return", "raw_text": "x", "masked_text": "x"})
    assert out["short_circuited"] is False


def test_threshold_above_top_short_circuits(monkeypatch):
    monkeypatch.setattr(retrieve, "THRESHOLD", 0.95)
    _patch(monkeypatch, [{"chunk_id": 1, "content": "c", "score": 0.9, "source_url": "u"}])
    out = retrieve.retrieve({"intent": "return", "raw_text": "x", "masked_text": "x"})
    assert out["short_circuited"] is True


def test_empty_chunks_short_circuits(monkeypatch):
    monkeypatch.setattr(retrieve, "THRESHOLD", 0.5)
    _patch(monkeypatch, [])
    out = retrieve.retrieve({"intent": "return", "raw_text": "x", "masked_text": "x"})
    assert out["chunks"] == []
    assert out["retrieval_score"] == 0.0
    assert out["short_circuited"] is True
