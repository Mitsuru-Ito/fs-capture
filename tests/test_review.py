import shutil
import unittest
from pathlib import Path
from unittest.mock import patch
import test_pipeline as fixtures
from fs_capture import core as c
from test_reliability import model


class ReviewTests(unittest.TestCase):
    setUp = fixtures.PipelineTests.setUp
    tearDown = fixtures.PipelineTests.tearDown
    fake_extract = fixtures.PipelineTests.fake_extract
    extract = fixtures.PipelineTests.extract

    def test_missing_output_cannot_be_approved(self):
        state = self.extract()
        shutil.rmtree(c.successful(state, 'extract'))
        with self.assertRaises(c.CaptureError):
            c.review(self.job, 'extract', True, 'must reject')
        with self.assertRaises(c.CaptureError):
            c.reviewed(c.read(self.job / 'state.json'), 'extract')

    def stage(self, name):
        state = self.extract()
        c.review(self.job, 'extract', True, 'fixture')
        output = self.root / name
        if name == 'sfm':
            model(output)
        else:
            output.mkdir()
            ply = output / 'training/step-000030000.ckpt/splat.ply'
            ply.parent.mkdir(parents=True)
            fixtures.gaussian(ply)
        config = c.read(self.job / 'job.json')
        validation = c.validate(name, output, config, state)
        state = c.read(self.job / 'state.json')
        state['stages'][name] = {'status': 'succeeded', 'output': str(output), 'validation': validation,
                                 'artifacts': c.fingerprint(output)}
        c.write(self.job / 'state.json', state)
        return output

    def test_corrupt_ply_cannot_be_approved(self):
        output = self.stage('train')
        (output / 'training/step-000030000.ckpt/splat.ply').write_bytes(b'broken')
        with self.assertRaises(c.CaptureError):
            c.review(self.job, 'train', True, 'must reject')

    def test_corrupt_sfm_cannot_be_approved(self):
        output = self.stage('sfm')
        for name in ('cameras.bin', 'images.bin', 'points3D.bin'):
            with self.subTest(file=name):
                path = output / 'sparse/0' / name
                original = path.read_bytes()
                path.write_bytes(b'broken')
                with self.assertRaises(c.CaptureError):
                    c.review(self.job, 'sfm', True, 'must reject')
                self.assertIn('error', c.read(self.job / 'state.json')['reviews']['sfm'])
                path.write_bytes(original)

    def test_valid_changed_file_cannot_be_approved(self):
        state = self.extract()
        (c.successful(state, 'extract') / 'images/cam0/00000.jpg').write_bytes(b'changed')
        with self.assertRaises(c.CaptureError):
            c.review(self.job, 'extract', True, 'edited data needs new job')

    def test_normal_review_is_read_only_and_failure_note_is_allowed(self):
        state = self.extract()
        output = c.successful(state, 'extract')
        before = c.fingerprint(output)
        c.review(self.job, 'extract', True, 'fixture')
        self.assertEqual(before, c.fingerprint(output))
        c.reviewed(c.read(self.job / 'state.json'), 'extract')
        shutil.rmtree(output)
        result = c.review(self.job, 'extract', False, 'output removed')
        self.assertEqual(result['status'], 'rejected')
        self.assertEqual(result['reviews']['extract']['note'], 'output removed')
