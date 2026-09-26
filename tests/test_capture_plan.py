import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from fs_capture.cli import main
from fs_capture import core as c

class BeforeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.plan=self.root/'before'
    def tearDown(self):self.tmp.cleanup()
    def cli(self,*args,code=0):
        with contextlib.redirect_stdout(io.StringIO()) as out,contextlib.redirect_stderr(io.StringIO()) as err:
            result=main(list(map(str,args)))
        self.assertEqual(result,code,err.getvalue())
        return json.loads(out.getvalue()) if code==0 else err.getvalue()
    def create(self):
        return self.cli('field-plan',self.plan,'--before-capture','--case','case','--site','site','--equipment','equipment','--purpose','layout','--date','2026-09-26','--revision','v1','--reviewer','planner','--target','入口','--need','設備までの位置関係が分かる','--target','正面','--need','周辺配管を区別できる')
    def job(self,name='job'):
        job=self.root/name;job.mkdir();source=job/'source.OSV';source.write_bytes(b'fixture only')
        c.write(job/'job.json',{'schemaVersion':1,'source':{'path':str(source),'sha256':c.digest(source),'bytes':source.stat().st_size},'settings':{'maskModel':None}})
        c.write(job/'state.json',{'configSha256':c.digest(job/'job.json'),'stages':{},'reviews':{}})
        return job
    def test_standalone_plan_needs_no_video_gpu_or_job(self):
        plan=self.create()
        self.assertEqual(plan['kind'],'pre-capture')
        self.assertFalse((self.plan/'job.json').exists());self.assertFalse((self.plan/'state.json').exists())
        self.assertEqual(plan['items'][0]['requirement'],'設備までの位置関係が分かる')
        self.assertIn('未撮影',(self.plan/'field-plan.html').read_text())
        self.cli('field-report',self.plan)
    def test_link_preserves_original_ids_and_requires_identity(self):
        original=self.create();before=c.fingerprint(self.plan);job=self.job();original_job=c.fingerprint(job)
        args=['field-link',self.plan,job,'--case','case','--site','site','--equipment','equipment','--reviewer','operator','--note','案件と設備を確認した']
        bad=list(args);bad[bad.index('--case')+1]='other';self.cli(*bad,code=1)
        self.assertEqual(c.fingerprint(job),original_job)
        linked=self.cli(*args)
        self.assertEqual(linked['items'],original['items']);self.assertEqual(c.fingerprint(self.plan),before)
        self.assertEqual(c.digest(job/'job.json'),original_job['job.json'])
        self.assertEqual(c.digest(job/'state.json'),original_job['state.json'])
        self.cli(*args,code=1)
        self.cli('field-check',job,'入口','--status','NOT_CAPTURED','--reviewer','operator','--note','未撮影')
        qa=c.read(job/'capture-qa.json')['items'][0]
        self.assertEqual(qa['fieldObservation']['targetId'],original['items'][0]['id'])
        self.assertEqual(c.fingerprint(self.plan),before)
    def test_missing_requirements_and_modified_snapshot_rejected(self):
        args=['field-plan',self.plan,'--before-capture','--case','case','--site','site','--equipment','equipment','--purpose','layout','--date','2026-09-26','--revision','v1','--reviewer','planner','--target','入口']
        self.cli(*args,code=1);self.assertFalse(self.plan.exists())
        self.create();job=self.job()
        self.cli('field-link',self.plan,job,'--case','case','--site','site','--equipment','equipment','--reviewer','operator','--note','confirmed')
        (job/'capture-plan-source.json').write_text('{}')
        self.cli('field-report',job,code=1)
