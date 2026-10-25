"""Legacy utility import path retained for the combined app and tests."""
"""Backward-compatible import path; implementation lives in app.shared.serialize."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.shared.serialize")
_sys.modules[__name__] = _implementation
