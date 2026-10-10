"""Native Python 3.14 dependencies must load on hardware without x86-64-v2."""

from __future__ import annotations

import sys

import pytest


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="Python 3.14-only runtime compatibility smoke")
def test_voice_enabled_python314_stack_imports():
    import av
    import ctranslate2
    import faster_whisper
    import numpy
    import onnxruntime

    assert numpy.__version__ == "2.3.5", "NumPy 2.4.x fails on older KVM CPUs lacking x86-64-v2"
    assert ctranslate2.__version__
    assert callable(faster_whisper.WhisperModel)
    assert av.__version__
    assert onnxruntime.__version__


@pytest.mark.skipif(sys.version_info[:2] != (3, 14), reason="Python 3.14-only runtime compatibility smoke")
def test_required_stdlib_native_modules_load():
    import bz2
    import lzma
    import sqlite3
    import ssl

    assert sqlite3.sqlite_version
    assert ssl.OPENSSL_VERSION
    assert lzma.compress(b"sample")
    assert bz2.compress(b"sample")
