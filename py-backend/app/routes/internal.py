"""Legacy route import path retained for compatibility."""
"""Backward-compatible import path; implementation lives in app.search_ai.routes.internal."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.search_ai.routes.internal")
_sys.modules[__name__] = _implementation
