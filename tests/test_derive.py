"""Train-only reuse contract. Synthetic data is not reconstruction evidence."""
import copy
from pathlib import Path
import shutil
import struct
import sys
import unittest
from unittest.mock import patch
from fs_capture import core as c
from fs_capture import derive as d
import test_pipeline as pipeline
from test_reliability import model
import test_collection
from fs_capture.collection import create_manifest


class DeriveTests(unittest.TestCase):
    fake_extract=pipeline.PipelineTests.fake_extract
    extract=pipeline.PipelineTests.extract
    tearDown=pipeline.PipelineTests.tearDown

    def setUp(self):
        pipeline.PipelineTests.setUp(self)
        self.extract();c.review(self.job,'extract',True,'synthetic only')
        self.config=c.read(self.job/'job.json');self.state=c.read(self.job/'state.json')
        folder=self.job/'attempts/sfm-fixture/output';model(folder)
        stats=c.inspect_artifacts('sfm',folder,self.config,self.state,write_metadata=True)
        self.state['stages']['sfm']={'status':'succeeded','output':str(folder),'attempt':str(folder.parent),'validation':stats,'artifacts':c.fingerprint(folder)}
        self.state['reviews']['sfm']={'passed':True,'note':'synthetic','at':1,'artifacts':self.state['stages']['sfm']['artifacts']}
        self.state['reviews']['train']={'passed':True,'note':'must not inherit visual judgement'}
        c.write(self.job/'state.json',self.state)
        c.write(self.job/'capture-qa.json',{'items':[{'status':'CAPTURED'}]})
        self.child=self.root/'child'

    def test_independent_copy_and_train_only_run_without_parent_qa(self):
        before=c.fingerprint(self.job)
        result=d.derive(self.job,self.child,iterations=2)
        self.assertEqual(c.fingerprint(self.job),before)
        cfg=c.read(self.child/'job.json');state=c.read(self.child/'state.json')
        self.assertEqual(cfg['schemaVersion'],1);self.assertEqual(cfg['settings']['iterations'],2)
        self.assertEqual(set(state['stages']),{'extract','sfm'})
        self.assertNotIn('train',state['reviews']);self.assertFalse((self.child/'capture-qa.json').exists())
        self.assertEqual(result['reused'],['extract','sfm'])
        for stage in result['reused']:
            a=c.successful(self.state,stage);b=c.successful(state,stage)
            self.assertEqual(c.fingerprint(a),c.fingerprint(b));c.reviewed(state,stage)
            name=next(iter(c.fingerprint(a)))
            self.assertNotEqual((a/name).stat().st_ino,(b/name).stat().st_ino)
            self.assertEqual(state['stages'][stage]['execution'],'reused_not_executed')
        def train(argv,log):
            self.assertEqual(argv[1:3],['train','360-camera'])
            self.assertTrue(Path(argv[argv.index('--image-dir')+1]).is_relative_to(self.child))
            out=Path(argv[argv.index('--output-dir-prefix')+1])/'training/step-000000002.ckpt'
            out.mkdir(parents=True);pipeline.gaussian(out/'splat.ply');log.write_text('synthetic trainer')
        with patch.object(c,'capture',return_value='spirula sfm 2026.9.20'),patch.object(c,'execute',side_effect=train) as execute:
            c.run(self.child,'train');self.assertEqual(execute.call_count,1)
        self.assertEqual(c.fingerprint(self.job),before)
        with self.assertRaises(c.CaptureError):c.run(self.child,'extract')

    def test_masked_parent_reuses_reviewed_mask(self):
        mask_model=self.root/'sam';mask_model.write_bytes(b'synthetic mask model')
        self.config['settings'].update(maskModel=str(mask_model),maskModelSha256=c.digest(mask_model))
        c.write(self.job/'job.json',self.config);self.state['configSha256']=c.digest(self.job/'job.json')
        out=self.job/'attempts/mask-fixture/output'
        for image in c.successful(self.state,'extract').glob('images/*/*.jpg'):
            target=out/'masks'/image.parent.name/(image.stem+'.png');target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(b'synthetic mask')
        self.state['stages']['mask']={'status':'succeeded','output':str(out),'attempt':str(out.parent),'artifacts':c.fingerprint(out)}
        self.state['reviews']['mask']={'passed':True,'artifacts':c.fingerprint(out)}
        c.write(self.job/'state.json',self.state)
        self.assertEqual(d.derive(self.job,self.child,2)['reused'],['extract','mask','sfm'])

    def test_bad_source_config_tool_and_legacy_snapshot_are_rejected(self):
        for mutate in ('source','config','tool','snapshot','review','artifact'):
            with self.subTest(mutate=mutate):
                state=copy.deepcopy(self.state);original_source=self.source.read_bytes();original_config=(self.job/'job.json').read_bytes()
                artifact=c.successful(state,'sfm')/'sparse/0/cameras.bin';raw=artifact.read_bytes()
                if mutate=='source':self.source.write_bytes(b'changed')
                if mutate=='config':(self.job/'job.json').write_bytes(original_config+b' ')
                if mutate=='tool':state['engine']['sha256']='changed'
                if mutate=='snapshot':state['stages']['sfm'].pop('artifacts')
                if mutate=='review':state['reviews']['sfm']['passed']=False
                if mutate=='artifact':artifact.write_bytes(b'bad')
                c.write(self.job/'state.json',state)
                with self.assertRaises((c.CaptureError,OSError)):d.derive(self.job,self.child,2)
                self.assertFalse(self.child.exists())
                self.source.write_bytes(original_source);(self.job/'job.json').write_bytes(original_config);artifact.write_bytes(raw)
        c.write(self.job/'state.json',self.state)

    def test_parent_lock_existing_target_and_invalid_change(self):
        with c.locked(self.job),self.assertRaises(c.CaptureError):d.derive(self.job,self.child,2)
        for n in (0,True,1.5,self.config['settings']['iterations']):
            with self.assertRaises(c.CaptureError):d.derive(self.job,self.child,n)
        with self.assertRaises(c.CaptureError):d.derive(self.job,self.job/'child',2)
        self.child.mkdir();(self.child/'keep').write_text('keep')
        with self.assertRaises(FileExistsError):d.derive(self.job,self.child,2)
        self.assertEqual((self.child/'keep').read_text(),'keep')

    def test_child_edit_does_not_touch_parent_and_blocks_train(self):
        d.derive(self.job,self.child,2);before=c.fingerprint(self.job)
        state=c.read(self.child/'state.json');(c.successful(state,'sfm')/'sparse/0/cameras.bin').write_bytes(b'changed')
        with patch.object(c,'execute') as execute,self.assertRaises(c.CaptureError):c.run(self.child,'train')
        execute.assert_not_called();self.assertEqual(c.fingerprint(self.job),before)

    def test_interrupted_copy_is_not_a_runnable_job(self):
        with patch.object(d.shutil,'copytree',side_effect=OSError('copy failed')),self.assertRaises(OSError):
            d.derive(self.job,self.child,2)
        self.assertEqual(c.read(self.child/'state.json')['status'],'derivation_failed')
        with self.assertRaisesRegex(c.CaptureError,'未完了'):c.run(self.child,'train')


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg integration tools absent')
class DeriveCollectionTests(unittest.TestCase):
    setUp=test_collection.CollectionTests.setUp
    tearDown=test_collection.CollectionTests.tearDown
    save=test_collection.CollectionTests.save
    masks=test_collection.CollectionTests.masks

    def test_manifest_mask_import_is_reused_without_extraction(self):
        self.masks();self.save()
        config=create_manifest(self.manifest,self.job,spirula=sys.executable)
        c.run(self.job,'extract');c.review(self.job,'extract',True,'synthetic media only')
        c.run(self.job,'mask');c.review(self.job,'mask',True,'synthetic all-white masks only')
        state=c.read(self.job/'state.json');folder=(self.job/'attempts/sfm-fixture/output').resolve();sparse=model(folder)
        (sparse/'cameras.bin').write_bytes(struct.pack('<QiiQQ8d',1,1,5,64,64,30,30,32,32,0,0,0,0))
        raw=(sparse/'images.bin').read_bytes()
        for old,new in zip((b'cam0/00000.jpg',b'cam0/00010.jpg',b'cam0/00020.jpg'),(b'cap0/cam0/00000.jpg',b'cap0/cam1/00000.jpg',b'cap1/cam0/00000.jpg')):
            raw=raw.replace(old+b'\0',new+b'\0')
        (sparse/'images.bin').write_bytes(raw)
        stats=c.inspect_artifacts('sfm',folder,config,state,write_metadata=True)
        state['engine']={'path':sys.executable,'sha256':c.digest(sys.executable),'version':'synthetic engine'}
        state['stages']['sfm']={'status':'succeeded','output':str(folder),'attempt':str(folder.parent),'validation':stats,'artifacts':c.fingerprint(folder)}
        state['reviews']['sfm']={'passed':True,'artifacts':c.fingerprint(folder)};c.write(self.job/'state.json',state)
        before=c.fingerprint(self.job);child=self.root/'child'
        with patch.object(c,'execute') as execute:
            d.derive(self.job,child,2);execute.assert_not_called()
        self.assertEqual(c.fingerprint(self.job),before)
        self.assertEqual(c.read(child/'job.json')['schemaVersion'],2)
        copied=c.read(child/'state.json')
        for stage in ('extract','mask','sfm'):
            self.assertEqual(copied['stages'][stage]['artifacts'],state['stages'][stage]['artifacts'])
        self.assertFalse((child/'capture-qa.json').exists())
