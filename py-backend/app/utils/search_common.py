"""Legacy utility import path retained for the combined app and tests."""
"""Backward-compatible import path; implementation lives in app.search_ai.services.search_common."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.search_ai.services.search_common")
_sys.modules[__name__] = _implementation
