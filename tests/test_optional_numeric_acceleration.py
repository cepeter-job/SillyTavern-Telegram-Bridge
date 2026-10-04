"""An optional vector accelerator must not prevent the bridge from starting."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize("error", ["ImportError", "RuntimeError"])
def test_vector_math_remains_available_when_numpy_cannot_initialize(error):
    source = f"""
import builtins
original_import = builtins.__import__
def import_without_numpy(name, *args, **kwargs):
    if name == "numpy":
        raise {error}("NumPy baseline CPU optimizations are unavailable")
    return original_import(name, *args, **kwargs)
builtins.__import__ = import_without_numpy
from bridge.rag_retrieval import cosine_similarity
assert abs(cosine_similarity([1.0, 2.0], [1.0, 2.0]) - 1.0) < 1e-12
assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == 0.0
assert cosine_similarity([0.0], [0.0]) == 0.0
"""
    result = subprocess.run([sys.executable, "-c", source], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
