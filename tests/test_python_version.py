import importlib
import sys

import pytest

import tailgate_core


def test_old_python_gets_a_clear_message(monkeypatch):
    monkeypatch.setattr(sys, "version_info", (3, 10, 14))
    monkeypatch.setattr(sys, "version", "3.10.14 (main)")
    with pytest.raises(RuntimeError, match=r"needs Python 3\.11 or newer .* this is 3\.10\.14"):
        importlib.reload(tailgate_core)
    monkeypatch.undo()
    importlib.reload(tailgate_core)  # leave the package loaded normally for other tests
