"""Legacy utility import path retained for the combined app and tests."""
"""Backward-compatible import path; implementation lives in app.brochureiq.services.search_index_client."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.brochureiq.services.search_index_client")
_sys.modules[__name__] = _implementation
