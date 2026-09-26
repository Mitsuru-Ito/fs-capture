import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from fs_capture import core as c
from fs_capture import field

class FieldTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.source=self.root/'input.osv';self.source.write_bytes(b'original')
        self.config={'schemaVersion':2,'captures':[{'captureId':'room','mode':'moving','frames':[0,10,20],'source':{'path':str(self.source),'sha256':c.digest(self.source),'bytes':8}}],'settings':{'imageWidth':100,'imageHeight':100,'maskModel':None}}
        c.write(self.root/'job.json',self.config)
        c.write(self.root/'state.json',{'configSha256':c.digest(self.root/'job.json'),'stages':{}})
    def tearDown(self):self.tmp.cleanup()
    def test_budget_counts_exceeds_and_unknown(self):
        result=field.preflight(self.root)
        self.assertEqual((result['images'],result['candidatePairs']),(6,15))
        self.assertEqual(result['gpuSeconds'],'UNKNOWN')
        with self.assertRaises(c.CaptureError):field.enforce(result,{'maxImages':5,'maxPairs':15,'minFreeBytes':0,'reason':'test'})
        unknown=dict(result,images='UNKNOWN')
        with self.assertRaises(c.CaptureError):field.enforce(unknown,field.DEFAULT_BUDGET)
        self.assertEqual(self.source.read_bytes(),b'original')
    def test_plan_missing_evidence_and_evaluation_contamination(self):
        plan=field.create_plan(self.root,'case','site','equipment','layout','2026-09-26','v1','operator',['Entrance','Front'])
        self.assertEqual(len(plan['items']),2)
        with self.assertRaises(c.CaptureError):field.check(self.root,'Entrance','CAPTURED','saw it','operator',[])
        with self.assertRaises(c.CaptureError):field.check(self.root,'Unknown','NOT_TESTED','pending','operator',[])
        with self.assertRaises(c.CaptureError):field.add_evaluation(self.root,self.source)
        copy=self.root/'renamed.jpg';copy.write_bytes(self.source.read_bytes())
        with self.assertRaises(c.CaptureError):field.add_evaluation(self.root,copy)
        evaluation=self.root/'heldout.jpg';evaluation.write_bytes(b'heldout')
        field.add_evaluation(self.root,evaluation)
        evaluation.write_bytes(b'changed')
        with self.assertRaises(c.CaptureError):field.preflight(self.root)
    def test_check_records_visibility_and_preserves_config(self):
        original=(self.root/'job.json').read_bytes()
        field.create_plan(self.root,'case','site','equipment','layout','2026-09-26','v1','operator',['Entrance'])
        field.check(self.root,'Entrance','OCCLUDED','blocked','operator',[],visibility='OCCLUDED')
        self.assertIn('OCCLUDED',(self.root/'field-plan.html').read_text())
        self.assertEqual(original,(self.root/'job.json').read_bytes())
        with self.assertRaises(c.CaptureError):field.create_plan(self.root,'case','site','equipment','layout','2026-09-26','v1','operator',[])
    def test_budget_blocks_execution_and_records_failure(self):
        field.set_budget(self.root,5,15,0,'deliberate small test budget')
        with patch.object(c,'execute') as execute:
            with self.assertRaisesRegex(c.CaptureError,'Budget blocked'):c.run(self.root,'extract')
            execute.assert_not_called()
        state=c.read(self.root/'state.json')
        self.assertEqual(state['stages']['extract']['status'],'failed')
        self.assertEqual(state['stages']['extract']['resourcePreflight']['images'],6)
    def test_unknown_disk_is_not_a_pass(self):
        with patch('fs_capture.field.shutil.disk_usage',side_effect=OSError('unknown')):
            result=field.preflight(self.root)
        self.assertEqual(result['freeBytes'],'UNKNOWN')
        with self.assertRaises(c.CaptureError):field.enforce(result,field.DEFAULT_BUDGET)
    def test_html_escapes_and_capture_history_is_complete(self):
        field.create_plan(self.root,'case','site','equipment','layout','2026-09-26','v1','operator',['<script>'])
        field.check(self.root,'<script>','OCCLUDED','first','operator',[],visibility='OCCLUDED')
        field.check(self.root,'<script>','NEEDS_CAPTURE','second','operator',[],visibility='RECONSTRUCTION_UNCERTAIN')
        entry=c.read(self.root/'capture-qa.json')['items'][0]
        self.assertEqual(entry['history'][0]['fieldObservation']['visibility'],'OCCLUDED')
        self.assertNotIn('<script>',(self.root/'field-plan.html').read_text())

    def test_cli_preflight_budget_and_plan_dispatch(self):
        import contextlib, io, json
        from fs_capture.cli import main
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(main(['preflight',str(self.root)]),0)
        self.assertEqual(json.loads(output.getvalue())['images'],6)
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['budget',str(self.root),'--max-images','20','--max-pairs','190','--min-free-bytes','0','--reason','pilot']),0)
            self.assertEqual(main(['field-plan',str(self.root),'--case','case','--site','site','--equipment','eq','--purpose','layout','--date','2026-09-26','--revision','v1','--reviewer','operator','--target','Entrance']),0)
            self.assertEqual(main(['field-check',str(self.root),'Entrance','--status','NOT_CAPTURED','--reviewer','operator','--note','scheduled']),0)
    def test_explicit_worklog_separates_human_and_machine(self):
        field.log_work(self.root,'mask','human-active',5,'operator','measured manually')
        result=field.log_work(self.root,'train','machine-wait',20,'operator','wall time')
        self.assertEqual(result['totalMinutes'],{'human-active':5,'machine-wait':20})
        with self.assertRaises(c.CaptureError):field.log_work(self.root,'train','machine-wait',float('nan'),'operator','invalid')
        self.assertEqual(len(c.read(self.root/'worklog.json')['items']),2)
    def test_unknown_extraction_requires_explicit_decision_but_sfm_never_does(self):
        result=field.preflight(self.root,'extract');result.update(images='UNKNOWN',candidatePairs='UNKNOWN')
        with self.assertRaises(c.CaptureError):field.enforce(result,field.DEFAULT_BUDGET)
        policy=dict(field.DEFAULT_BUDGET,allowUnknownExtraction=True,reason='count by decoding')
        field.enforce(result,policy)
        result['stage']='sfm'
        with self.assertRaises(c.CaptureError):field.enforce(result,policy)
    def test_copy_preflight_checks_snapshots_and_counts_bytes(self):
        state=c.read(self.root/'state.json')
        for name in ('extract','sfm'):
            folder=self.root/name;folder.mkdir();(folder/'fixture').write_bytes(b'abc')
            state['stages'][name]={'status':'succeeded','output':str(folder),'artifacts':c.fingerprint(folder)}
        c.write(self.root/'state.json',state)
        self.assertEqual(field.preflight(self.root,copy_upstream=True)['plannedCopyBytes'],6)
        (self.root/'sfm/fixture').write_bytes(b'changed')
        with self.assertRaises(c.CaptureError):field.preflight(self.root,copy_upstream=True)
