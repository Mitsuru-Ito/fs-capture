import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from fs_capture import diagnostics as d
from fs_capture import core as c
from fs_capture.cli import main
from test_pipeline import gaussian, metadata


class DiagnosticsTests(unittest.TestCase):
    def fake_capture(self, argv):
        if 'devices' in argv:
            return '0  Example GPU  integrated  64.0 G  0000  ok\n'
        return 'spirula sfm 2026.9.20\n' if 'sfm' in argv else 'version fixture\n'

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.tools = patch.object(d, 'tool', return_value=sys.executable)
        self.capture = patch.object(d, 'capture', side_effect=self.fake_capture)
        self.tools.start()
        self.mock_capture = self.capture.start()

    def tearDown(self):
        self.capture.stop()
        self.tools.stop()
        self.tmp.cleanup()

    def test_installed_tools_do_not_imply_capabilities(self):
        result, code = d.diagnose()
        self.assertEqual(code, 0)
        self.assertTrue(all(x['status'] == 'NOT_TESTED' for x in result['checks'].values()))
        self.assertEqual(self.mock_capture.call_count, 3)

    def test_gpu_only_does_not_imply_decode_or_training(self):
        result, code = d.diagnose(check_gpu=True)
        self.assertEqual(code, 0)
        self.assertEqual(result['checks']['gpu']['status'], 'PASSED')
        for key in ('decode', 'training'):
            self.assertEqual(result['checks'][key]['status'], 'NOT_TESTED')

    def test_cpu_unusable_and_unknown_output_never_pass_gpu(self):
        for output, expected in (('0  CPU  cpu  1.0 G  uuid  ok\n', 'FAILED'),
                                  ('0  GPU  discrete  1.0 G  uuid  unsupported\n', 'FAILED'),
                                  ('unrecognized output', 'UNKNOWN')):
            with self.subTest(output=output):
                with patch.object(d, 'capture', side_effect=lambda argv: output if 'devices' in argv else self.fake_capture(argv)):
                    result, code = d.diagnose(check_gpu=True)
                self.assertEqual(code, 1)
                self.assertEqual(result['checks']['gpu']['status'], expected)

    def test_missing_selected_decoder_fails(self):
        def resolve(name):
            if name == 'ffmpeg':
                raise c.CaptureError('missing ffmpeg')
            return sys.executable
        with patch.object(d, 'tool', side_effect=resolve):
            result, code = d.diagnose(decoder='ffmpeg')
        self.assertEqual(code, 1)
        self.assertFalse(result['tools']['ffmpeg']['exists'])
        self.assertEqual(result['checks']['decode']['status'], 'NOT_TESTED')

    def test_missing_source_reports_failure_not_decoder_success(self):
        result, code = d.diagnose(source=str(self.root / 'missing.OSV'), decoder='ffmpeg')
        self.assertEqual(code, 1)
        self.assertEqual(result['checks']['input']['status'], 'FAILED')
        self.assertEqual(result['checks']['decode']['status'], 'NOT_TESTED')

    def test_empty_decode_output_is_failure(self):
        source = self.root / 'test.OSV'
        source.write_bytes(b'fixture')
        with patch.object(d, 'probe', return_value=(metadata(), 30)):
            result, code = d.diagnose(source=source, decoder='ffmpeg')
        self.assertEqual(code, 1)
        self.assertEqual(result['checks']['decode']['status'], 'FAILED')

    def test_timeout_is_recorded(self):
        with patch.object(d, 'capture', side_effect=subprocess.TimeoutExpired('tool', 60)):
            result, code = d.diagnose(check_gpu=True)
        self.assertEqual(code, 1)
        self.assertEqual(result['tools']['spirula']['status'], 'FAILED')
        self.assertTrue(result['tools']['spirula']['exists'])
        self.assertEqual(result['checks']['gpu']['status'], 'FAILED')

    def test_training_record_is_verified_and_tamper_rejected(self):
        source = self.root / 'source.OSV'
        source.write_bytes(b'synthetic-source')
        job = self.root / 'job'
        with patch.object(c, 'probe', return_value=(metadata(), 30)):
            c.create(source, job, iterations=1, spirula=sys.executable, ffprobe=sys.executable)
        result, code = d.diagnose(job=job)
        self.assertEqual(result['checks']['training']['status'], 'NOT_TESTED')
        output = job / 'attempts/train-fixture/output'
        ply = output / 'training/step-000000001.ckpt/splat.ply'
        ply.parent.mkdir(parents=True)
        gaussian(ply)
        state = c.read(job / 'state.json')
        state['engine'] = {'path': sys.executable, 'sha256': c.digest(sys.executable)}
        state['stages']['train'] = {'status': 'succeeded', 'output': str(output), 'validation': c.check_ply(ply)}
        c.write(job / 'state.json', state)
        result, code = d.diagnose(job=job)
        self.assertEqual(code, 0)
        self.assertEqual(result['checks']['training']['status'], 'PASSED')
        ply.write_bytes(ply.read_bytes()[:-1])
        result, code = d.diagnose(job=job)
        self.assertEqual(code, 1)
        self.assertEqual(result['checks']['training']['status'], 'FAILED')

    def test_cli_keeps_json_and_not_tested_status(self):
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = main(['doctor', '--decoder', 'ffmpeg'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(stream.getvalue())['checks']['training']['status'], 'NOT_TESTED')


class DecodeIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg unavailable')
    def test_actual_two_track_first_frame_decode(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'synthetic.OSV'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'testsrc2=size=64x64:rate=10:duration=0.2',
                            '-map', '0:v', '-map', '0:v', '-c:v', 'mpeg4', '-f', 'mp4', str(source)], check=True)
            original = c.digest(source)
            result, _ = d.diagnose(spirula='definitely-missing-spirula', source=source, decoder='ffmpeg')
            self.assertEqual(result['checks']['decode']['status'], 'PASSED')
            self.assertEqual(c.digest(source), original)
            self.assertEqual(result['checks']['training']['status'], 'NOT_TESTED')
            self.assertEqual(list(Path(directory).iterdir()), [source])
