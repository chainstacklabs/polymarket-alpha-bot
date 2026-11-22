"""Test-wide setup.

Several pipeline modules read their LLM model names at import time and raise
if unset. Tests never call the LLM, so placeholder names let those modules be
imported. Real values from the environment are left untouched.
"""

import os

os.environ.setdefault("IMPLICATIONS_MODEL", "test/placeholder-model")
os.environ.setdefault("VALIDATION_MODEL", "test/placeholder-model")
