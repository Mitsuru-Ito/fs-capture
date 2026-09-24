import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

from fs_capture import core as c


def metadata():
    stream = {"codec_type": "video", "width": 4000, "height": 4000,
              "avg_frame_rate": "30/1", "r_frame_rate": "30/1"}
    return {"streams": [dict(stream), dict(stream),
                        {"codec_type": "video", "disposition": {"attached_pic": 1}}]}


def gaussian(path, point_cloud=False, nonfinite=False):
    props = ["x", "y", "z"]
    if not point_cloud:
        props += ["opacity"] + [f"f_dc_{i}" for i in range(3)]
        props += [f"scale_{i}" for i in range(3)] + [f"rot_{i}" for i in range(4)]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 1\n"
    header += "".join(f"property float {p}\n" for p in props) + "end_header\n"
    values = [float("nan") if nonfinite else 0.] + [0.] * (len(props) - 1)
    path.write_bytes(header.encode() + struct.pack("<" + "f" * len(props), *values))


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "素材 $(do-not-run).OSV"
        self.source.write_bytes(b"fixture-container-not-real-video")
        self.job = self.root / "job"
        with patch.object(c, "probe", return_value=(metadata(), 30.0)):
            c.create(self.source, self.job, spirula=sys.executable, ffprobe=sys.executable)

    def tearDown(self):
        self.tmp.cleanup()

    def fake_extract(self, argv, log):
        images = Path(argv[argv.index("--out") + 1])
        for cam in ("cam0", "cam1"):
            (images / cam).mkdir(parents=True)
            for i in (0, 10, 20):
                (images / cam / f"{i:05d}.jpg").write_bytes(b"test-image-placeholder")
        log.write_text("test double, not GPU reconstruction\n")

    def extract(self):
        with patch.object(c, "capture", return_value="spirula sfm 2026.9.20"), patch.object(c, "execute", side_effect=self.fake_extract):
            return c.run(self.job, "extract")

    def test_attached_cover_is_not_a_third_lens(self):
        with patch.object(c, "capture", return_value=json.dumps(metadata())):
            self.assertEqual(c.probe(self.source, "ffprobe")[1], 30)

    def test_mismatched_tracks_rejected(self):
        data = metadata()
        data["streams"][1]["avg_frame_rate"] = "60/1"
        with patch.object(c, "capture", return_value=json.dumps(data)), self.assertRaises(c.CaptureError):
            c.probe(self.source, "ffprobe")

    def test_input_is_one_argument_and_360_conversion_disabled(self):
        argv = c.commands(c.read(self.job / "job.json"), c.read(self.job / "state.json"), "extract", self.root)[0]
        self.assertEqual(argv[3], str(self.source))
        self.assertEqual(argv[argv.index("--360") + 1], "off")
        self.assertIn("--sync", argv)

    def test_changed_input_is_rejected(self):
        self.source.write_bytes(b"changed")
        with self.assertRaisesRegex(c.CaptureError, "入力ファイル"):
            c.run(self.job, "extract")

    def test_changed_config_is_rejected(self):
        cfg = c.read(self.job / "job.json")
        cfg["settings"]["iterations"] = 4
        c.write(self.job / "job.json", cfg)
        with self.assertRaisesRegex(c.CaptureError, "設定"):
            c.run(self.job, "extract")

    def test_quality_gate_and_artifact_tampering(self):
        self.extract()
        state = c.read(self.job / "state.json")
        with self.assertRaises(c.CaptureError):
            c.reviewed(state, "extract")
        c.review(self.job, "extract", True, "test-only review")
        state = c.read(self.job / "state.json")
        c.reviewed(state, "extract")
        (c.successful(state, "extract") / "images/cam0/00000.jpg").write_bytes(b"changed")
        with self.assertRaises(c.CaptureError):
            c.reviewed(state, "extract")

    def test_failure_keeps_attempt_then_retry_uses_new_directory(self):
        with patch.object(c, "capture", return_value="spirula sfm 2026.9.20"), patch.object(c, "execute", side_effect=c.CaptureError("GPU failed")):
            with self.assertRaises(c.CaptureError):
                c.run(self.job, "extract")
        before = c.read(self.job / "state.json")["stages"]["extract"]
        self.assertEqual(before["status"], "failed")
        state = self.extract()
        self.assertNotEqual(before["attempt"], state["stages"]["extract"]["attempt"])
        self.assertTrue(Path(before["attempt"]).is_dir())
        self.assertEqual(state["status"], "awaiting_review")

    def test_zero_exit_without_artifacts_is_failure(self):
        with patch.object(c, "capture", return_value="2026.9.20"), patch.object(c, "execute"):
            with self.assertRaises(c.CaptureError):
                c.run(self.job, "extract")
        self.assertEqual(c.read(self.job / "state.json")["status"], "failed")

    def test_cancellation_persisted_and_lock_released(self):
        with patch.object(c, "capture", return_value="2026.9.20"), patch.object(c, "execute", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                c.run(self.job, "extract")
        self.assertEqual(c.read(self.job / "state.json")["status"], "cancelled")
        self.assertFalse((self.job / ".lock").exists())

    def test_concurrent_run_rejected(self):
        with c.locked(self.job), self.assertRaises(c.CaptureError):
            c.run(self.job, "extract")

    def test_point_cloud_and_nonfinite_ply_rejected(self):
        path = self.root / "splat.ply"
        for kwargs in ({"point_cloud": True}, {"nonfinite": True}):
            gaussian(path, **kwargs)
            with self.assertRaises(c.CaptureError):
                c.check_ply(path)
        gaussian(path)
        self.assertEqual(c.check_ply(path)["gaussians"], 1)
        path.write_bytes(path.read_bytes()[:-1])
        with self.assertRaises(c.CaptureError):
            c.check_ply(path)

    def test_intermediate_checkpoint_not_accepted(self):
        path = self.root / "training/step-000000010.ckpt"
        path.mkdir(parents=True)
        gaussian(path / "splat.ply")
        with self.assertRaisesRegex(c.CaptureError, "最終反復"):
            c.validate("train", self.root, c.read(self.job / "job.json"))

    def test_mask_names_map_to_source_frames(self):
        state = self.extract()
        output = self.root / "mask-output"
        for cam in ("cam0", "cam1"):
            folder = output / "masks" / cam
            folder.mkdir(parents=True)
            for i in range(3):
                (folder / f"frame_{i:05d}.png").write_bytes(b"test-mask")
        self.assertEqual(c.validate("mask", output, {}, state)["masks"], 6)
        self.assertTrue((output / "masks/cam1/00020.png").is_file())

    def test_pose_parser_rejects_truncation(self):
        path = self.root / "images.bin"
        body = struct.pack("<Q", 1) + struct.pack("<i7di", 1, 1, 0, 0, 0, 0, 0, 0, 1)
        body += b"cam0/00000.jpg\0" + struct.pack("<Q", 0)
        path.write_bytes(body)
        self.assertEqual(c.read_poses(path)[0]["image"], "cam0/00000.jpg")
        path.write_bytes(body[:-2])
        with self.assertRaises((c.CaptureError, struct.error)):
            c.read_poses(path)

    def test_process_failure_is_reported_with_log(self):
        log = self.root / "process.log"
        with self.assertRaises(c.CaptureError):
            c.execute([sys.executable, "-c", "print('failure'); raise SystemExit(7)"], log)
        self.assertIn("failure", log.read_text())


if __name__ == "__main__":
    unittest.main()
