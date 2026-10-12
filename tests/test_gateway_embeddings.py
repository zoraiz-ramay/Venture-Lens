import json
from types import SimpleNamespace

import pytest
import requests

from core import llm as llm_mod, tool_search, web


@pytest.fixture(autouse=True)
def isolated_embeddings(monkeypatch):
    monkeypatch.delenv("EMBEDDING_MODEL", raising=False)
    monkeypatch.setattr(web, "_cached", lambda *args, **kwargs: None)
    monkeypatch.setattr(web, "_store", lambda *args, **kwargs: None)
    monkeypatch.setattr(llm_mod, "_rate_gate", None)
    monkeypatch.setattr(llm_mod.time, "sleep", lambda delay: None)


def response(values=None, status=200):
    result = requests.Response()
    result.status_code = status
    result._content = json.dumps({"embedding": {"values": values or []}}).encode()
    result.headers["retry-after"] = "0"
    return result


def gateway_client():
    return llm_mod.LLMClient(key="test-key", base_url="https://llm.sdc.siemens.cloud/v1")


@pytest.mark.parametrize("provider, base_url, expected", [
    ("openai", "https://llm.sdc.siemens.cloud/v1", "google/gemini-embedding-2"),
    ("openai", "https://llm.sdc.siemens.cloud/v1/", "google/gemini-embedding-2"),
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai/", "gemini-embedding-001"),
    ("openai", "https://api.openai.com/v1", "text-embedding-3-small"),
    ("openai", "https://other.example/v1", ""),
    ("none", "https://llm.sdc.siemens.cloud/v1", ""),
])
def test_embedding_default_is_specific_to_the_endpoint(provider, base_url, expected):
    client = llm_mod.LLMClient.__new__(llm_mod.LLMClient)
    client.provider, client.base_url = provider, base_url
    assert client.embedding_model() == expected


def test_explicit_embedding_model_remains_authoritative(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    assert gateway_client().embedding_model() == "text-embedding-3-small"


def test_gateway_uses_native_gemini_route_and_siemens_auth(monkeypatch):
    seen = []

    def post(url, **kwargs):
        seen.append((url, kwargs))
        return response([0.1, 0.2])

    monkeypatch.setattr(requests, "post", post)
    client = gateway_client()
    client.last_error = "previous error"
    assert client.embed(["robot simulation"], dims=2, cache=False) == [[0.1, 0.2]]
    assert client.last_error == ""
    url, options = seen[0]
    assert url == "https://llm.sdc.siemens.cloud/v1beta/models/gemini-embedding-2:embedContent"
    assert options["headers"] == {"x-api-key": "test-key"}  # secret-scan:allow (placeholder test value)
    assert options["json"] == {"content": {"parts": [{"text": "robot simulation"}]}, "outputDimensionality": 2}
    assert options["timeout"] == llm_mod.LLM_TIMEOUT


def test_native_catalog_requests_preserve_input_order_and_throttle_each_text(monkeypatch):
    gated = []
    monkeypatch.setattr(llm_mod, "_rate_gate", lambda: gated.append(True))
    monkeypatch.setattr(requests, "post", lambda url, **kwargs: response(
        [float(kwargs["json"]["content"]["parts"][0]["text"])]))
    assert gateway_client().embed(["3", "1", "2"], dims=1, cache=False) == [[3.0], [1.0], [2.0]]
    assert len(gated) == 3


def test_gateway_rate_limit_retries_then_succeeds(monkeypatch):
    replies = [response(status=429), response([0.1, 0.2])]
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: replies.pop(0))
    assert gateway_client().embed(["startup"], dims=2, cache=False) == [[0.1, 0.2]]
    assert replies == []


def test_failed_gateway_call_returns_no_vectors(monkeypatch):
    monkeypatch.setattr(requests, "post", lambda *args, **kwargs: response(status=403))
    client = gateway_client()
    assert client.embed(["startup"], dims=2, cache=False) is None
    assert "403" in client.last_error


@pytest.mark.parametrize("failure", [requests.ConnectionError, requests.Timeout])
def test_native_transport_failure_retries_then_succeeds(monkeypatch, failure):
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise failure("temporary disconnect")
        return response([0.1, 0.2])

    monkeypatch.setattr(requests, "post", post)
    assert gateway_client().embed(["startup"], dims=2, cache=False) == [[0.1, 0.2]]
    assert len(calls) == 2


def test_native_transport_retries_are_bounded(monkeypatch):
    calls = []

    def post(*args, **kwargs):
        calls.append(True)
        raise requests.ConnectionError("persistent disconnect")

    monkeypatch.setattr(requests, "post", post)
    client = gateway_client()
    assert client.embed(["startup"], dims=2, cache=False) is None
    assert len(calls) == llm_mod.EMBED_RETRIES


def test_cached_gateway_vector_makes_no_request(monkeypatch):
    monkeypatch.setattr(web, "_cached", lambda *args, **kwargs: [[0.1, 0.2]])

    def unexpected_request(*args, **kwargs):
        pytest.fail("cached embedding reached the network")

    monkeypatch.setattr(requests, "post", unexpected_request)
    assert gateway_client().embed(["startup"], dims=2) == [[0.1, 0.2]]


def test_explicit_openai_embedding_still_uses_the_compatible_endpoint(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-small")
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(embedding=[0.1, 0.2])], usage=None)

    client = gateway_client()
    client._client = SimpleNamespace(embeddings=SimpleNamespace(create=create))
    assert client.embed(["startup"], dims=2, cache=False) == [[0.1, 0.2]]
    assert calls[0]["model"] == "text-embedding-3-small"


def test_old_gemini_index_is_not_reused_for_gateway_model(tmp_path, monkeypatch):
    import numpy as np

    monkeypatch.setattr(tool_search, "INDEX_DIR", tmp_path)
    tool_search._loaded.clear()
    metadata = {"model": "gemini-embedding-001", "dims": 2, "checksum": "catalog", "names": ["Tool"]}
    (tmp_path / "meta.json").write_text(json.dumps(metadata), encoding="utf-8")
    np.save(tmp_path / "vectors.npy", np.asarray([[1.0, 0.0]], dtype="float16"))
    catalog = {"available": True, "checksum": "catalog"}
    assert tool_search.load_index(catalog, gateway_client().embedding_model(), 2) is None


def test_gemini_and_gateway_indexes_live_side_by_side(tmp_path, monkeypatch):
    """Production embeds through the gateway and local runs through Gemini: one checkout must
    serve both, each loading the index built with its own model."""
    monkeypatch.setattr(tool_search, "INDEX_DIR", tmp_path)
    monkeypatch.setattr(tool_search, "_dims", lambda: 2)
    tool_search._loaded.clear()
    catalog = {"available": True, "checksum": "catalog", "entries": [{"name": "Tool"}]}

    class Embedder:
        def __init__(self, model, vector):
            self.model, self.vector, self.last_error = model, vector, ""

        def embedding_model(self):
            return self.model

        def embed(self, texts, dims=2, cache=True):
            return [self.vector for _ in texts]

    tool_search.build_index(catalog, Embedder("gemini-embedding-001", [1.0, 0.0]))
    tool_search.build_index(catalog, Embedder("google/gemini-embedding-2", [0.0, 1.0]))
    local = tool_search.load_index(catalog, "gemini-embedding-001", 2)
    gateway = tool_search.load_index(catalog, "google/gemini-embedding-2", 2)
    assert local.meta["model"] == "gemini-embedding-001" and local.vectors[0][0] == 1.0
    assert gateway.meta["model"] == "google/gemini-embedding-2" and gateway.vectors[0][1] == 1.0


def test_openai_direct_gets_the_key_only_as_a_bearer_token_and_its_own_embedding_model():
    # The gateway reads the key from x-api-key; OpenAI itself does not, so the key is not copied
    # into a header it would ignore.
    direct = llm_mod.LLMClient(key="test-key", base_url="https://api.openai.com/v1")  # secret-scan:allow
    assert "x-api-key" not in {k.lower() for k in direct._client._custom_headers}
    assert direct.embedding_model() == "text-embedding-3-small"
    gateway = gateway_client()
    assert gateway._client._custom_headers.get("x-api-key") == "test-key"  # secret-scan:allow
    assert gateway.embedding_model() == "google/gemini-embedding-2"
