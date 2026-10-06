import pathlib
import runpy

import pytest

from core import config


@pytest.mark.parametrize("local_value, exported_value, expected", [
    (None, None, "workspace-value"),
    ("checkout-value", None, "checkout-value"),
    (None, "exported-value", "exported-value"),
    ("checkout-value", "exported-value", "exported-value"),
])
def test_nested_checkout_env_loading_preserves_precedence(tmp_path, monkeypatch, local_value,
                                                         exported_value, expected):
    source = pathlib.Path(config.__file__)
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (tmp_path / ".env").write_text("OPENAI_API_KEY=workspace-value\n", encoding="utf-8")
    if local_value:
        (checkout / ".env").write_text(f"OPENAI_API_KEY={local_value}\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    if exported_value:
        monkeypatch.setenv("OPENAI_API_KEY", exported_value)
    original_resolve = pathlib.Path.resolve

    def resolve(path, *args, **kwargs):
        return checkout / "core" / "config.py" if path == source else original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "resolve", resolve)
    runpy.run_path(str(source))
    import os
    assert os.environ["OPENAI_API_KEY"] == expected