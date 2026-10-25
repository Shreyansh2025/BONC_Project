"""Legacy utility import path retained for the combined app and tests."""
"""Backward-compatible import path; implementation lives in app.search_ai.services.b2b_search."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.search_ai.services.b2b_search")
_sys.modules[__name__] = _implementation
