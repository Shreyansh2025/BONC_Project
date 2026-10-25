"""Backward-compatible import path; implementation lives in app.shared.logger."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.shared.logger")
_sys.modules[__name__] = _implementation
