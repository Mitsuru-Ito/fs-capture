"""Boundary regressions from the Phase A review; fixtures are synthetic."""
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

from fs_capture import core as c
import test_pipeline as pipeline


def model(folder):
    sparse = folder / 'sparse/0'
    sparse.mkdir(parents=True)
    (sparse / 'cameras.bin').write_bytes(struct.pack('<QiiQQ8d', 1, 1, 5, 1920, 1920,
                                                  900, 900, 960, 960, 0, 0, 0, 0))
    data = struct.pack('<Q', 3)
    for i in range(3):
        data += struct.pack('<i7di', i + 1, 1, 0, 0, 0, i, 0, 0, 1)
        data += f'cam0/{i * 10:05d}.jpg'.encode() + b'\0'
        data += struct.pack('<Qddq', 1, 500, 600, 7)
    (sparse / 'images.bin').write_bytes(data)
    (sparse / 'points3D.bin').write_bytes(struct.pack('<QQ3d3BdQ', 1, 7, 0, 0, 2, 10, 20, 30, .1, 3)
                                          + b''.join(struct.pack('<ii', i, 0) for i in (1, 2, 3)))
    return sparse


class PreflightTests(unittest.TestCase):
    setUp = pipeline.PipelineTests.setUp
    tearDown = pipeline.PipelineTests.tearDown
    fake_extract = pipeline.PipelineTests.fake_extract
    extract = pipeline.PipelineTests.extract

    def test_missing_source_is_recorded_and_retry_preserves_failure(self):
        original = self.source.read_bytes()
        self.source.unlink()
        with patch.object(c, 'execute') as execute:
            with self.assertRaises((OSError, c.CaptureError)):
                c.run(self.job, 'extract')
            execute.assert_not_called()
        state = c.read(self.job / 'state.json')
        self.assertEqual(state['status'], 'failed')
        failure = state['stages']['extract']
        self.assertEqual(failure['phase'], 'preflight')
        self.assertIn(self.source.name, failure['error'])
        self.assertIn('finishedAt', failure)
        self.assertFalse((self.job / '.lock').exists())
        self.source.write_bytes(original)
        after = self.extract()
        self.assertNotEqual(failure['attempt'], after['stages']['extract']['attempt'])
        self.assertEqual(c.read(Path(failure['attempt']) / 'result.json'), failure)

    def test_successful_stage_is_not_overwritten_by_rejected_rerun(self):
        before = self.extract()
        self.source.unlink()
        with self.assertRaises(c.CaptureError):
            c.run(self.job, 'extract')
        self.assertEqual(before, c.read(self.job / 'state.json'))

    def test_invalid_config_and_missing_tool_are_recorded(self):
        config = (self.job / 'job.json').read_text()
        (self.job / 'job.json').write_text('{broken')
        with self.assertRaises(ValueError):
            c.run(self.job, 'extract')
        before = c.read(self.job / 'state.json')['stages']['extract']
        self.assertEqual(before['status'], 'failed')
        self.assertEqual(before['phase'], 'preflight')
        (self.job / 'job.json').write_text(config)
        with patch.object(c, 'tool', side_effect=c.CaptureError('missing tool')):
            with self.assertRaises(c.CaptureError):
                c.run(self.job, 'extract')
        after = c.read(self.job / 'state.json')['stages']['extract']
        self.assertEqual(after['error'], 'missing tool')
        self.assertNotEqual(before['attempt'], after['attempt'])
        self.assertFalse((self.job / '.lock').exists())

    def test_sfm_rejects_header_only_camera_and_point_files(self):
        state = self.extract()
        folder = self.root / 'sfm'
        sparse = model(folder)
        for name in ('cameras.bin', 'points3D.bin'):
            with self.subTest(file=name):
                path = sparse / name
                original = path.read_bytes()
                path.write_bytes(struct.pack('<Q', 1))
                with self.assertRaises(c.CaptureError):
                    c.validate('sfm', folder, {}, state)
                self.assertFalse((folder / 'cameras.json').exists())
                path.write_bytes(original)
        self.assertEqual(c.validate('sfm', folder, {}, state)['registeredImages'], 3)


class BinaryModelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.folder = model(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_complete_model(self):
        poses, stats = c.check_sfm_model(self.folder)
        self.assertEqual(len(poses), 3)
        self.assertEqual(stats, {'cameras': 1, 'points3D': 1, 'observations': 3})

    def test_each_binary_truncation_and_trailing_bytes(self):
        for name in ('cameras.bin', 'images.bin', 'points3D.bin'):
            path = self.folder / name
            original = path.read_bytes()
            for data in (original[:7], original[:8], original[:-1], original + b'extra'):
                with self.subTest(name=name, size=len(data)):
                    path.write_bytes(data)
                    with self.assertRaises(c.CaptureError):
                        c.check_sfm_model(self.folder)
            path.write_bytes(original)

    def test_bad_camera_model_and_nonfinite_parameters(self):
        path = self.folder / 'cameras.bin'
        original = path.read_bytes()
        for offset, fmt, value in ((12, '<i', 99), (32, '<d', float('nan'))):
            data = bytearray(original)
            struct.pack_into(fmt, data, offset, value)
            path.write_bytes(data)
            with self.assertRaises(c.CaptureError):
                c.check_sfm_model(self.folder)

    def test_missing_camera_reference(self):
        path = self.folder / 'cameras.bin'
        data = bytearray(path.read_bytes())
        struct.pack_into('<i', data, 8, 42)
        path.write_bytes(data)
        with self.assertRaisesRegex(c.CaptureError, 'カメラ'):
            c.check_sfm_model(self.folder)

    def test_nonfinite_point_and_bad_tracks(self):
        path = self.folder / 'points3D.bin'
        original = path.read_bytes()
        for offset, fmt, value in ((16, '<d', float('inf')), (59, '<i', 99),
                                   (63, '<i', 99), (51, '<Q', 2**63)):
            with self.subTest(offset=offset):
                data = bytearray(original)
                struct.pack_into(fmt, data, offset, value)
                path.write_bytes(data)
                with self.assertRaises(c.CaptureError):
                    c.check_sfm_model(self.folder)

    def test_zero_based_ids_are_supported(self):
        path = self.folder / 'cameras.bin'
        data = bytearray(path.read_bytes())
        struct.pack_into('<i', data, 8, 0)
        path.write_bytes(data)
        path = self.folder / 'images.bin'
        data = bytearray(path.read_bytes())
        stride = 64 + len(b'cam0/00000.jpg\0') + 8 + 24
        for i in range(3):
            struct.pack_into('<i', data, 8 + i * stride, i)
            struct.pack_into('<i', data, 8 + i * stride + 60, 0)
        path.write_bytes(data)
        path = self.folder / 'points3D.bin'
        data = bytearray(path.read_bytes())
        for i in range(3):
            struct.pack_into('<i', data, 59 + i * 8, i)
        path.write_bytes(data)
        self.assertEqual(c.check_sfm_model(self.folder)[0][0]['id'], 0)


if __name__ == '__main__':
    unittest.main()
