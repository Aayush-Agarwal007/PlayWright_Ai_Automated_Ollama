import pytest
import requests
from services.ollama_service import OllamaError, OllamaService


class Resp:
    def __init__(self, status=200, body=None, text=None):
        self.status_code, self._body, self._text = status, body, text
    def raise_for_status(self):
        if self.status_code >= 400: raise requests.HTTPError(str(self.status_code))
    def json(self):
        return self._body if self._body is not None else {"message": {"content": self._text}}


class Session:
    def __init__(self, posts=(), get=None): self.posts, self.get_resp, self.n = list(posts), get, 0
    def post(self, *a, **k):
        r = self.posts[min(self.n, len(self.posts) - 1)]; self.n += 1
        if isinstance(r, Exception): raise r
        return r
    def get(self, *a, **k):
        if isinstance(self.get_resp, Exception): raise self.get_resp
        return self.get_resp


CFG = {"ollama_model": "qwen2.5:3b", "ollama_retries": 2}


def test_health_ok_and_missing_model():
    ok = OllamaService(CFG, Session(get=Resp(body={"models": [{"name": "qwen2.5:3b"}]}))).check()
    assert ok.ok
    missing = OllamaService(CFG, Session(get=Resp(body={"models": [{"name": "llama3"}]}))).check()
    assert missing.reachable and not missing.model_available and "ollama pull" in missing.message


def test_health_unreachable():
    st = OllamaService(CFG, Session(get=requests.ConnectionError())).check()
    assert not st.reachable and "ollama serve" in st.message


def test_malformed_then_valid_json_is_retried(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = Session([Resp(text="not json"), Resp(text='{"answers": []}')])
    assert OllamaService(CFG, s).chat_json("sys", {}) == {"answers": []} and s.n == 2


def test_gives_up_after_retries(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    s = Session([requests.Timeout()])
    with pytest.raises(OllamaError):
        OllamaService(CFG, s).chat_json("sys", {})
    assert s.n == 3


def test_missing_model_404():
    with pytest.raises(OllamaError, match="ollama pull"):
        OllamaService(CFG, Session([Resp(status=404)])).chat_json("s", {})


def test_legacy_ollama_url_config():
    svc = OllamaService({"ollama_url": "http://127.0.0.1:11434/api/chat"})
    assert svc.base_url == "http://127.0.0.1:11434"
