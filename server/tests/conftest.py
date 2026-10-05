import pytest
from fastapi.testclient import TestClient

from server.app import create_app


def legacy_meeting(mid="1700000000000abcd"):
    return {
        "id": mid, "title": "測試會議", "createdAt": 1700000000000, "updatedAt": 1700000000000,
        "group": "g1", "durationSec": 125.5, "audioMode": "single", "audioParts": [],
        "transcript": [
            {"speaker": "A", "text": "第一句", "t": "01:02"},
            {"speaker": "B", "text": "第二句", "fi": 1},
            {"speaker": "A", "text": "第三句", "t": 3725},
        ],
        "summary": {"actionItems": ["待辦一"], "mainPoints": ["重點一"],
                    "qa": [{"q": "問？", "a": "答。"}]},
        "terms": {"items": []},
    }


@pytest.fixture
def meeting():
    return legacy_meeting()


@pytest.fixture
def data_dir(tmp_path):
    return tmp_path / "data"


@pytest.fixture
def static_dir(tmp_path):
    d = tmp_path / "static"
    d.mkdir()
    (d / "index.html").write_text("<html>DD-INDEX</html>", encoding="utf-8")
    return d


@pytest.fixture
def client(data_dir, static_dir, monkeypatch):
    monkeypatch.setenv("MEETINGS_STATIC", str(static_dir))
    app = create_app(data_dir, start_threads=False)
    return TestClient(app)
