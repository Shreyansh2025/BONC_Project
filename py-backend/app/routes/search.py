"""Legacy route import path retained for app.main and tests."""
"""Backward-compatible import path; implementation lives in app.search_ai.routes.search."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.search_ai.routes.search")
_sys.modules[__name__] = _implementation
