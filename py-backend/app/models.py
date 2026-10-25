"""Backward-compatible import path; implementation lives in app.brochureiq.models."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.brochureiq.models")
_sys.modules[__name__] = _implementation
