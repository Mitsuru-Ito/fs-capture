"""Real CLI paths with synthetic media, not real-room quality evidence."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch
import test_collection as fixtures
from fs_capture import core as c, field
from fs_capture.cli import main

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
class EvidenceCliTests(unittest.TestCase):
    setUp=fixtures.CollectionTests.setUp
    tearDown=fixtures.CollectionTests.tearDown
    save=fixtures.CollectionTests.save
    def cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()) as output, contextlib.redirect_stderr(io.StringIO()) as error:
            code=main([str(x) for x in args])
        self.assertEqual(code,0,error.getvalue())
        return json.loads(output.getvalue())
    def prepare(self):
        self.cli('init-manifest',self.manifest,self.job)
        self.cli('run',self.job,'extract')
        self.cli('field-plan',self.job,'--case','case','--site','site','--equipment','equipment','--purpose','layout','--date','2026-09-26','--revision','v1','--reviewer','tester','--target','Entrance')
        image=next(c.successful(c.read(self.job/'state.json'),'extract').rglob('*.jpg'))
        photo=self.root/'photo.jpg';shutil.copyfile(image,photo)
        self.cli('field-check',self.job,'Entrance','--status','CAPTURED','--photo',photo,'--visibility','OBSERVED','--publication','APPROVED','--reviewer','tester','--note','observed synthetic photo')
        return photo
    def evidence(self):
        self.cli('report',self.job)
        return c.read(self.job/'field-evidence.json')
    def test_photo_current_validity_and_history_unchanged(self):
        photo=self.prepare();original=photo.read_bytes();qa=(self.job/'capture-qa.json').read_bytes()
        self.assertEqual(self.evidence()['items'][0]['evidenceStatus'],'VALID')
        photo.write_bytes(b'different file under same name')
        current=self.evidence();self.assertEqual(current['items'][0]['evidenceStatus'],'CHANGED');self.assertEqual(current['validCapturedCount'],0)
        photo.unlink();self.assertEqual(self.evidence()['items'][0]['evidenceStatus'],'MISSING')
        photo.write_bytes(original);self.assertEqual(self.evidence()['items'][0]['evidenceStatus'],'VALID')
        self.assertEqual(qa,(self.job/'capture-qa.json').read_bytes())
        self.assertIn('APPROVED',(self.job/'field-plan.html').read_text())
    def test_old_hash_missing_is_unverified(self):
        self.prepare();qa=c.read(self.job/'capture-qa.json')
        next(x for x in qa['items'] if x['label']=='Entrance')['referencePhotos'][0].pop('sha256')
        c.write(self.job/'capture-qa.json',qa)
        self.assertEqual(self.evidence()['items'][0]['evidenceStatus'],'UNVERIFIED')
    def test_new_manifest_parent_retry_and_no_overwrite(self):
        output=self.root/'new'/'work'/'manifest.json';source=self.data['captures'][0]['source'];before=c.digest(source)
        args=['capture-manifest',output,'--source',source,'--seconds','1','--sample-fps','3']
        with patch.object(Path,'mkdir',side_effect=PermissionError('permission denied')):
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(main(list(map(str,args))),1)
            self.assertIn('permission',err.getvalue().lower())
        self.assertFalse(output.exists())
        self.cli(*args);saved=output.read_bytes()
        with contextlib.redirect_stderr(io.StringIO()):self.assertEqual(main(list(map(str,args))),1)
        self.assertEqual(output.read_bytes(),saved);self.assertEqual(c.digest(source),before)
