"""Legacy utility import path retained for the combined app and tests."""
"""Backward-compatible import path; implementation lives in app.brochureiq.services.image_extractor."""
import importlib as _importlib
import sys as _sys

_implementation = _importlib.import_module("app.brochureiq.services.image_extractor")
_sys.modules[__name__] = _implementation
