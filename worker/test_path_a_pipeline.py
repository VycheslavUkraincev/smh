#!/usr/bin/env python3
"""Unit tests for Path A pipeline — mocks only, no GPU / no torch / no cv2.

Canon: LaMa → GFPGAN|RestoreFormer++ → Real-ESRGAN → [DDColor]
Run:  python3 worker/test_path_a_pipeline.py
"""
from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

# Worker dir on sys.path
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import path_a_pipeline as pa  # noqa: E402


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
    """Clear Path A ENV between tests."""

    _KEYS = (
        "FACE_MODEL",
        "ENABLE_DDCOLOR",
        "PATH_A_STRICT",
        "FAIL_SOFT",
        "PATH_A_ALLOW_STUB",
        "PATH_A_DRY_RUN",
        "PATH_A_DRYRUN",
        "PATH_A_DOWNLOAD",
        "WEIGHTS_DIR",
        "COLORIZE",
        "GEN_STACK",
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


class TestCanonAndEnv(_EnvGuard):
    def test_stages_order_canon(self):
        self.assertEqual(pa.STAGES, ["lama", "face", "realesrgan", "ddcolor"])

    def test_face_model_aliases(self):
        os.environ["FACE_MODEL"] = "gfpgan"
        self.assertEqual(pa.face_model(), "gfpgan")
        os.environ["FACE_MODEL"] = "rfpp"
        self.assertEqual(pa.face_model(), "restoreformer")
        os.environ["FACE_MODEL"] = "restoreformer++"
        self.assertEqual(pa.face_model(), "restoreformer")

    def test_plan_stages_default_no_ddcolor(self):
        self.assertEqual(
            pa.plan_stages("authentic"),
            ["lama", "face", "realesrgan"],
        )

    def test_plan_stages_with_ddcolor_env(self):
        os.environ["ENABLE_DDCOLOR"] = "1"
        self.assertEqual(
            pa.plan_stages("authentic"),
            ["lama", "face", "realesrgan", "ddcolor"],
        )

    def test_plan_stages_colorize_flag_overrides(self):
        self.assertEqual(
            pa.plan_stages("authentic", colorize=True),
            ["lama", "face", "realesrgan", "ddcolor"],
        )
        os.environ["ENABLE_DDCOLOR"] = "1"
        self.assertEqual(
            pa.plan_stages("authentic", colorize=False),
            ["lama", "face", "realesrgan"],
        )

    def test_plan_stages_modern_auto_includes_ddcolor(self):
        # modern + COLORIZE=auto → ddcolor listed
        os.environ["COLORIZE"] = "auto"
        self.assertIn("ddcolor", pa.plan_stages("modern"))

    def test_fail_soft_default_and_strict(self):
        self.assertTrue(pa.fail_soft())
        self.assertFalse(pa.path_a_strict())
        os.environ["FAIL_SOFT"] = "0"
        self.assertFalse(pa.fail_soft())
        self.assertTrue(pa.path_a_strict())
        os.environ.pop("FAIL_SOFT", None)
        os.environ["PATH_A_STRICT"] = "1"
        self.assertFalse(pa.fail_soft())
        self.assertTrue(pa.path_a_strict())

    def test_path_a_dry_run_alias(self):
        self.assertFalse(pa.path_a_allow_stub())
        os.environ["PATH_A_DRY_RUN"] = "1"
        self.assertTrue(pa.path_a_allow_stub())
        os.environ.pop("PATH_A_DRY_RUN", None)
        os.environ["PATH_A_DRYRUN"] = "1"
        self.assertTrue(pa.path_a_allow_stub())


class TestWeightsHelpers(_EnvGuard):
    def test_expected_weights_respect_face_model(self):
        os.environ["FACE_MODEL"] = "gfpgan"
        keys = [k for k, _ in pa.expected_weight_files()]
        self.assertEqual(keys, ["lama", "gfpgan", "realesrgan"])
        os.environ["FACE_MODEL"] = "restoreformer"
        keys = [k for k, _ in pa.expected_weight_files()]
        self.assertEqual(keys, ["lama", "restoreformer", "realesrgan"])

    def test_missing_weights(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["WEIGHTS_DIR"] = td
            miss = pa.missing_weights()
            self.assertIn("lama", miss)
            self.assertIn("gfpgan", miss)
            self.assertIn("realesrgan", miss)

    def test_resolve_weight_file(self):
        with tempfile.TemporaryDirectory() as td:
            os.environ["WEIGHTS_DIR"] = td
            self.assertIsNone(pa.resolve_weight_file("lama"))
            p = os.path.join(td, "big-lama.pt")
            with open(p, "wb") as f:
                f.write(b"x")
            self.assertEqual(pa.resolve_weight_file("lama"), p)


class TestOrchestratorMocks(_EnvGuard):
    def test_run_stages_order_with_injected_fns(self):
        """Inject mock stage fns — verify canon call order, no real ML."""
        order = []

        def _mk(name):
            def _fn(inp, out):
                order.append(name)
                shutil.copy2(inp, out)
                return pa.StageResult(name, "mock", True, "ok", inp, out)

            return _fn

        stage_fns = {
            "lama": _mk("lama"),
            "face": _mk("face"),
            "realesrgan": _mk("realesrgan"),
            "ddcolor": _mk("ddcolor"),
        }
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            dst = os.path.join(td, "out.jpg")
            res = pa.run_stages(
                src,
                dst,
                ["lama", "face", "realesrgan"],
                fidelity=0.5,
                work_dir=os.path.join(td, "work"),
                stage_fns=stage_fns,
            )
            self.assertTrue(res.ok)
            self.assertEqual(order, ["lama", "face", "realesrgan"])
            self.assertTrue(os.path.isfile(dst))
            self.assertEqual([s.name for s in res.stages], ["lama", "face", "realesrgan"])

    def test_run_stages_includes_ddcolor(self):
        order = []

        def _mk(name):
            def _fn(inp, out):
                order.append(name)
                shutil.copy2(inp, out)
                return pa.StageResult(name, "mock", True, "ok", inp, out)

            return _fn

        stage_fns = {n: _mk(n) for n in ("lama", "face", "realesrgan", "ddcolor")}
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            dst = os.path.join(td, "out.jpg")
            res = pa.run_stages(
                src,
                dst,
                ["lama", "face", "realesrgan", "ddcolor"],
                work_dir=os.path.join(td, "work"),
                stage_fns=stage_fns,
            )
            self.assertTrue(res.ok)
            self.assertEqual(order, ["lama", "face", "realesrgan", "ddcolor"])
            self.assertTrue(res.used_ddcolor)

    def test_stage_stub_identity_under_allow_stub(self):
        os.environ["PATH_A_ALLOW_STUB"] = "1"
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            out = os.path.join(td, "out.jpg")
            for fn, name in (
                (pa.stage_lama, "lama"),
                (lambda i, o: pa.stage_face(i, o, fidelity=0.5), "face"),
                (pa.stage_realesrgan, "realesrgan"),
            ):
                dst = os.path.join(td, f"{name}.jpg")
                sr = fn(src, dst)
                self.assertTrue(sr.ok)
                self.assertEqual(sr.backend, "stub")
                self.assertTrue(os.path.isfile(dst))

    def test_restore_path_a_bytes_stub(self):
        os.environ["PATH_A_ALLOW_STUB"] = "1"
        raw = _tiny_jpeg_bytes()
        out = pa.restore_path_a(
            img_bytes=raw,
            prompt="test",
            fidelity=0.5,
            preserve_identity=True,
            colorize=False,
        )
        self.assertIsInstance(out, (bytes, bytearray))
        self.assertGreater(len(out), 100)
        # JPEG SOI
        self.assertEqual(out[:2], b"\xff\xd8")

    def test_restore_path_a_path_api(self):
        os.environ["PATH_A_ALLOW_STUB"] = "1"
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            dst = os.path.join(td, "out.jpg")
            res = pa.restore_path_a(src, dst, mode="authentic")
            self.assertIsInstance(res, pa.PipelineResult)
            self.assertTrue(res.ok)
            self.assertEqual([s.name for s in res.stages], ["lama", "face", "realesrgan"])
            self.assertTrue(os.path.isfile(dst))

    def test_restore_path_a_positional_bytes_compat(self):
        """runpod_handler may pass bytes as first positional."""
        os.environ["PATH_A_ALLOW_STUB"] = "1"
        raw = _tiny_jpeg_bytes()
        out = pa.restore_path_a(raw, prompt="x", fidelity=0.5, preserve_identity=True)
        self.assertEqual(out[:2], b"\xff\xd8")

    def test_restore_path_a_dry_run_env(self):
        """PATH_A_DRY_RUN=1 exercises I/O + stage order without GPU claim."""
        os.environ["PATH_A_DRY_RUN"] = "1"
        with tempfile.TemporaryDirectory() as td:
            src = _write_tiny_jpeg(os.path.join(td, "in.jpg"))
            dst = os.path.join(td, "out.jpg")
            res = pa.restore_path_a(src, dst, mode="authentic")
            self.assertTrue(res.ok)
            self.assertEqual([s.name for s in res.stages], ["lama", "face", "realesrgan"])
            self.assertTrue(all(s.backend == "stub" for s in res.stages))
            self.assertTrue(os.path.isfile(dst))


class TestHandlerDispatch(_EnvGuard):
    def test_gen_stack_legacy_default(self):
        # Fresh import of module-level default
        import importlib

        import runpod_handler as rh

        importlib.reload(rh)
        self.assertEqual(rh.GEN_STACK, "legacy")

    def test_gen_stack_path_a_routes(self):
        import runpod_handler as rh

        os.environ["GEN_STACK"] = "path_a"
        os.environ["PATH_A_ALLOW_STUB"] = "1"
        raw = _tiny_jpeg_bytes()
        with mock.patch.object(rh, "_restore_legacy") as leg:
            out = rh._restore(raw, "p", 0.5, True)
            leg.assert_not_called()
        self.assertEqual(out[:2], b"\xff\xd8")

    def test_gen_stack_legacy_routes(self):
        import runpod_handler as rh

        os.environ["GEN_STACK"] = "legacy"
        raw = _tiny_jpeg_bytes()
        with mock.patch.object(rh, "_restore_legacy", return_value=b"\xff\xd8LEGACY") as leg:
            out = rh._restore(raw, "p", 0.5, True)
            leg.assert_called_once()
        self.assertEqual(out, b"\xff\xd8LEGACY")


if __name__ == "__main__":
    unittest.main(verbosity=2)
