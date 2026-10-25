"""Backward-compatible import path; implementation lives in app.brochureiq.config."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.brochureiq.config")
_sys.modules[__name__] = _implementation
