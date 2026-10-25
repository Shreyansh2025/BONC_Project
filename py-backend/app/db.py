"""Backward-compatible import path; implementation lives in app.shared.db."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.shared.db")
_sys.modules[__name__] = _implementation
