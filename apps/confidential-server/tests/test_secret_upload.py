from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest


def _uploader() -> ModuleType:
    path = Path(__file__).resolve().parents[3] / "infra/gcp/upload-secret-version.py"
    spec = importlib.util.spec_from_file_location("upload_secret", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upload_passes_value_only_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    module = _uploader()
    secret = "a" * 64
    monkeypatch.setattr(module.sys, "argv", ["upload-secret-version.py", "shared"])
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: secret)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: Any) -> None:
        calls.append(command)
        assert secret not in " ".join(command)
        assert kwargs == {"input": secret.encode(), "check": True}
        assert "simpleunmark-confidential-shared-secret" in command
        assert "--data-file=-" in command

    monkeypatch.setattr(module.subprocess, "run", run)
    module.main()
    assert len(calls) == 1


@pytest.mark.parametrize("case", ["noninteractive", "mismatch", "short", "newline"])
def test_invalid_input_never_calls_gcloud(monkeypatch: pytest.MonkeyPatch, case: str) -> None:
    module = _uploader()
    monkeypatch.setattr(module.sys, "argv", ["upload-secret-version.py", "shared"])
    monkeypatch.setattr(module.sys.stdin, "isatty", lambda: case != "noninteractive")
    values = iter(
        {
            "noninteractive": ["a" * 64, "a" * 64],
            "mismatch": ["a" * 64, "b" * 64],
            "short": ["short", "short"],
            "newline": ["a" * 64 + "\n", "a" * 64 + "\n"],
        }[case]
    )
    monkeypatch.setattr(module.getpass, "getpass", lambda prompt: next(values))

    def unexpected(*args: Any, **kwargs: Any) -> None:
        pytest.fail("gcloud must not run")

    monkeypatch.setattr(module.subprocess, "run", unexpected)
    with pytest.raises(SystemExit) as caught:
        module.main()
    assert caught.value.code == 2
