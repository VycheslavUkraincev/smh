#!/usr/bin/env python3
"""Unit tests: generate.py GEN_STACK alignment with Path A canon.

Default stack stays legacy (prod-safe). path_a routes to real stages
via local/gpu backends. FAIL_SOFT / PATH_A_DRY_RUN still OK.
Run:  python3 worker/test_generate_stack.py
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import unittest
from unittest import mock

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# common.py requires these at import time; offline unit tests use stubs only.
for _k, _v in {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SECRET": "test-secret",
    "SPACES_KEY": "test",
    "SPACES_SECRET": "test",
}.items():
    os.environ.setdefault(_k, _v)

import generate as gen  # noqa: E402
import path_a_pipeline as pa  # noqa: E402
import runpod_handler as rh  # noqa: E402


def _tiny_jpeg_bytes(w: int = 32, h: int = 32) -> bytes:
    from PIL import Image

    im = Image.new("RGB", (w, h), (90, 70, 50))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _write_tiny_jpeg(path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "wb") as f:
        f.write(_tiny_jpeg_bytes())
    return path


class _EnvGuard(unittest.TestCase):
    _KEYS = (
        "FACE_MODEL",
        "ENABLE_DDCOLOR",
        "PATH_A_STRICT",
        "FAIL_SOFT",
        "PATH_A_ALLOW_STUB",
        "PATH_A_DRY_RUN",
        "PATH_A_DRYRUN",
        "GEN_STACK",
        "GEN_PROVIDER",
    )

    def setUp(self):
        self._saved = {k: os.environ.get(k) for k in self._KEYS}
        for k in self._KEYS:
            os.environ.pop(k, None)

    def tearDown(self):
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


class TestGenerateStackSelect(_EnvGuard):
    def test_default_stack_legacy(self):
        self.assertEqual(gen.resolve_gen_stack(), "legacy")
        self.assertFalse(gen.is_path_a_stack())

    def test_path_a_aliases(self):
        for alias in ("path_a", "path-a", "patha", "a"):
            self.assertEqual(gen.resolve_gen_stack(alias), "path_a")

    def test_select_default_api_legacy_is_fal(self):
        self.assertEqual(gen.select_generate_backend("api", "legacy"), "fal_legacy")

    def test_select_api_path_a_stays_fal_safe(self):
        # DO api worker must not silently stub if GEN_STACK=path_a alone
        self.assertEqual(gen.select_generate_backend("api", "path_a"), "fal_legacy")

    def test_select_gpu_any_stack(self):
        self.assertEqual(gen.select_generate_backend("gpu", "legacy"), "gpu")
        self.assertEqual(gen.select_generate_backend("gpu", "path_a"), "gpu")

    def test_select_local_path_a(self):
        self.assertEqual(gen.select_generate_backend("local", "path_a"), "path_a_local")
        self.assertEqual(gen.select_generate_backend("path_a", "path_a"), "path_a_local")

    def test_mode_mapping(self):
        self.assertEqual(gen.resolve_path_a_mode({"mode": "restore"}), "authentic")
        self.assertEqual(gen.resolve_path_a_mode({"mode": "revive"}), "modern")
        self.assertEqual(gen.resolve_path_a_mode("authentic"), "authentic")

    def test_path_a_run_dry_run(self):
        os.environ["PATH_A_DRY_RUN"] = "1"
        os.environ["GEN_STACK"] = "path_a"
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            dst = os.path.join(td, "out.jpg")
            out = gen.path_a_run(src, dst, mode="authentic", prompt="x")
            self.assertEqual(out, dst)
            self.assertTrue(os.path.isfile(dst))

    def test_fail_soft_dry_run_still_ok(self):
        os.environ["FAIL_SOFT"] = "1"
        os.environ["PATH_A_DRY_RUN"] = "1"
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            dst = os.path.join(td, "out.jpg")
            res = pa.restore_path_a(src, dst, mode="authentic")
            self.assertTrue(res.ok)
            self.assertEqual([s.name for s in res.stages], ["lama", "face", "realesrgan"])


class TestHandlerRequestStack(_EnvGuard):
    def test_resolve_stack_default_legacy(self):
        self.assertEqual(rh.resolve_stack(None), "legacy")
        self.assertEqual(rh.resolve_stack("path_a"), "path_a")

    def test_request_gen_stack_overrides_env(self):
        os.environ["GEN_STACK"] = "legacy"
        os.environ["PATH_A_ALLOW_STUB"] = "1"
        raw = _tiny_jpeg_bytes()
        with mock.patch.object(rh, "_restore_legacy") as leg:
            out = rh._restore(raw, "p", 0.5, True, gen_stack="path_a")
            leg.assert_not_called()
        self.assertEqual(out[:2], b"\xff\xd8")

    def test_legacy_route_unchanged(self):
        os.environ["GEN_STACK"] = "legacy"
        raw = _tiny_jpeg_bytes()
        with mock.patch.object(rh, "_restore_legacy", return_value=b"\xff\xd8LEGACY") as leg:
            out = rh._restore(raw, "p", 0.5, True)
            leg.assert_called_once()
        self.assertEqual(out, b"\xff\xd8LEGACY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
