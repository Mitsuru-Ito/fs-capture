import shutil
from pathlib import Path
import unittest
from unittest.mock import patch
import test_collection as fixtures
from fs_capture import core as c, field
from fs_capture.collection import create_manifest

@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg tools absent')
class FieldMediaTests(unittest.TestCase):
    setUp=fixtures.CollectionTests.setUp
    tearDown=fixtures.CollectionTests.tearDown
    save=fixtures.CollectionTests.save
    def test_seconds_draft_extract_capture_reference_and_report(self):
        original=[c.digest(x['source']) for x in self.data['captures']]
        draft=self.root/'draft.json'
        field.draft_manifest(draft,[x['source'] for x in self.data['captures']],sample_fps=3,seconds=1,targets=['Entrance'])
        create_manifest(draft,self.job)
        field.create_plan(self.job,'case','site','equipment','layout','2026-09-26','v1','operator',['Entrance'])
        c.run(self.job,'extract')
        refs=field.capture_references(self.job,'capture_01','cam0')
        self.assertEqual(len(refs),4)
        field.check(self.job,'Entrance','CAPTURED','observed all adopted images','operator',refs,visibility='OBSERVED')
        from fs_capture.report import report
        output=report(self.job)
        self.assertIn('field-plan.html',Path(output['report']).read_text())
        self.assertEqual(original,[c.digest(x['source']) for x in self.data['captures']])
    def test_manifest_rejects_evaluation_source_and_role(self):
        self.data['evaluationSources']=[self.data['captures'][0]['source']];self.save()
        with self.assertRaises(c.CaptureError):create_manifest(self.manifest,self.job)
        self.assertFalse(self.job.exists())
        del self.data['evaluationSources'];self.data['captures'][0]['role']='evaluation-only';self.save()
        with self.assertRaises(c.CaptureError):create_manifest(self.manifest,self.job)
        self.assertFalse(self.job.exists())
    def test_photo_is_decoded_and_missing_reference_rejected(self):
        self.save();create_manifest(self.manifest,self.job);c.run(self.job,'extract')
        field.create_plan(self.job,'case','site','equipment','layout','2026-09-26','v1','operator',['Entrance'])
        output=c.successful(c.read(self.job/'state.json'),'extract')
        photo=next(output.rglob('*.jpg'))
        field.check(self.job,'Entrance','CAPTURED','photo checked','operator',['photo:'+str(photo)])
        entry=next(x for x in c.read(self.job/'capture-qa.json')['items'] if x['label']=='Entrance')
        self.assertEqual(entry['referencePhotos'][0]['sha256'],c.digest(photo))
        with self.assertRaises(c.CaptureError):field.check(self.job,'Entrance','CAPTURED','missing','operator',['extract:missing'])
