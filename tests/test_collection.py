import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from fs_capture import core as c
from fs_capture.collection import create_manifest
from fs_capture.cli import main
from fs_capture.report import report, capture_check
from fs_capture.media import image_pixels


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'FFmpeg integration tools absent')
class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.job = self.root / 'job'
        self.manifest = self.root / 'manifest.json'
        captures = []
        for i, color in enumerate(('red', 'blue')):
            folder = self.root / str(i)
            folder.mkdir()
            source = folder / 'same.OSV'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:size=64x64:rate=10:duration=1',
                            '-map','0:v','-map','0:v','-c:v','mpeg4','-f','mp4',str(source)], check=True)
            captures.append({'captureId': f'cap{i}', 'source': str(source), 'mode': 'fixed', 'frames': [0]})
        self.data = {'schemaVersion': 1, 'captures': captures, 'imageWidth': 64, 'checklist': ['入口 <script>']}
        self.save()

    def tearDown(self):
        self.tmp.cleanup()

    def save(self):
        c.write(self.manifest, self.data)

    def init(self):
        self.save()
        return create_manifest(self.manifest, self.job)

    def masks(self, size=64, gray='white'):
        root = self.root / 'masks'
        for cid in ('cap0', 'cap1'):
            for cam in ('cam0', 'cam1'):
                path = root / cid / cam / '00000.png'
                path.parent.mkdir(parents=True, exist_ok=True)
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={gray}:size={size}x{size}',
                                '-frames:v','1','-update','1',str(path)],check=True)
        self.data['masking'] = {'root': str(root), 'convention': 'white-keep', 'categories': ['person','fisheye-boundary']}
        return root

    def test_same_basename_fixed_pair_mapping_and_cli(self):
        self.save()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['init-manifest',str(self.manifest),str(self.job)]),0)
        state = c.run(self.job, 'extract')
        output = c.successful(state, 'extract')
        frames = c.read(output/'source-index.json')['frames']
        self.assertEqual([x['captureId'] for x in frames], ['cap0','cap1'])
        self.assertEqual([x['frameIndex'] for x in frames], [0,0])
        self.assertTrue((output/'images/cap0/cam1/00000.jpg').is_file())
        original = c.fingerprint(output)
        c.review(self.job,'extract',True,'synthetic only')
        self.assertEqual(original,c.fingerprint(output))
        command=c.commands(c.read(self.job/'job.json'),state,'sfm',self.root/'sfm')[0]
        self.assertIn('dual-fisheye=cap0/cam0,cap0/cam1',command)
        self.assertIn('dual-fisheye=cap1/cam0,cap1/cam1',command)
        self.assertNotIn('--telemetry',command)

    def test_moving_range_preserves_original_frames(self):
        self.data['captures'][0].update(mode='moving', range={'startFrame':1,'endFrameExclusive':9,'stride':3})
        del self.data['captures'][0]['frames']
        self.init()
        state=c.run(self.job,'extract')
        index=c.read(c.successful(state,'extract')/'source-index.json')['frames']
        self.assertEqual([x['frameIndex'] for x in index if x['captureId']=='cap0'],[1,4,7])
        self.assertEqual(index[2]['approximateSeconds'],.7)

    def test_duplicate_id_missing_input_mismatched_conditions_and_duplicate_source(self):
        original=json.loads(json.dumps(self.data))
        cases=[('captureId','CAP0'),('source',str(self.root/'missing.OSV')),('source',self.data['captures'][0]['source'])]
        for key,value in cases:
            with self.subTest(key=key,value=value):
                self.data=json.loads(json.dumps(original));self.data['captures'][1][key]=value
                with self.assertRaises(c.CaptureError):self.init()
                self.assertFalse(self.job.exists())
        self.data=original
        real_probe=c.probe
        def changed(path, executable):
            meta,fps=real_probe(path,executable)
            if '/1/' in str(path):fps=20
            return meta,fps
        with patch.object(c,'probe',side_effect=changed), self.assertRaises(c.CaptureError):self.init()

    def test_invalid_selections(self):
        for mode,frames in [('fixed',[0,1]),('moving',[0]),('moving',[2,1,0]),('fixed',[999]),('fixed',[True])]:
            with self.subTest(mode=mode,frames=frames):
                self.data['captures'][0].update(mode=mode,frames=frames)
                with self.assertRaises(c.CaptureError):self.init()

    def test_manual_mask_import_report_and_change_rejected(self):
        masks=self.masks()
        self.init();c.run(self.job,'extract');c.review(self.job,'extract',True,'synthetic')
        state=c.run(self.job,'mask')
        output=c.successful(state,'mask')
        self.assertEqual(c.digest(output/'masks/cap0/cam0/00000.png'),c.digest(masks/'cap0/cam0/00000.png'))
        self.assertEqual(state['stages']['mask']['validation']['warnings'],4)
        before=c.fingerprint(output)
        c.review(self.job,'mask',True,'all-white warning reviewed for fixture only')
        self.assertEqual(before,c.fingerprint(output))
        capture_check(self.job,'入口 <script>','NEEDS_CAPTURE','追加視点が必要','tester')
        html=Path(report(self.job)['report']).read_text()
        self.assertIn('data:image/png;base64,',html)
        self.assertIn('NEEDS_CAPTURE',html)
        self.assertIn('入口 &lt;script&gt;',html)
        self.assertNotIn('https://',html)
        (masks/'cap0/cam0/00000.png').write_bytes(b'changed')
        with self.assertRaises(c.CaptureError):c.run(self.job,'sfm')
        self.assertEqual(c.read(self.job/'state.json')['stages']['sfm']['phase'],'preflight')

    def test_mask_missing_extra_inverted_and_wrong_dimensions(self):
        masks=self.masks(size=32)
        self.data['masking']['convention']='black-keep'
        with self.assertRaises(c.CaptureError):self.init()
        self.data['masking']['convention']='white-keep'
        extra=masks/'extra.png';shutil.copyfile(masks/'cap0/cam0/00000.png',extra)
        with self.assertRaises(c.CaptureError):self.init()
        extra.unlink()
        missing=masks/'cap0/cam0/00000.png'
        content=missing.read_bytes();missing.unlink()
        with self.assertRaises(c.CaptureError):self.init()
        missing.write_bytes(content)
        self.init();c.run(self.job,'extract');c.review(self.job,'extract',True,'fixture')
        with self.assertRaisesRegex(c.CaptureError,'寸法'):c.run(self.job,'mask')
        self.assertEqual(c.read(self.job/'state.json')['status'],'failed')

    def test_corrupt_and_nonbinary_images_rejected(self):
        path=self.root/'bad.png';path.write_bytes(b'not image')
        with self.assertRaises(c.CaptureError):image_pixels(path,mask=True)
        masks=self.masks(gray='gray')
        with self.assertRaisesRegex(c.CaptureError,'二値'):image_pixels(masks/'cap0/cam0/00000.png',mask=True)

    def test_failed_extract_retry_new_attempt(self):
        self.init()
        with patch.object(c,'execute',side_effect=c.CaptureError('test failure')):
            with self.assertRaises(c.CaptureError):c.run(self.job,'extract')
        before=c.read(self.job/'state.json')['stages']['extract']
        after=c.run(self.job,'extract')['stages']['extract']
        self.assertNotEqual(before['attempt'],after['attempt'])
        self.assertTrue((Path(before['attempt'])/'result.json').is_file())
