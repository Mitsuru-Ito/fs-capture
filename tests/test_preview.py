"""Synthetic contract tests, not evidence of reconstruction or visual quality."""
import base64
import copy
import hashlib
import io
import json
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch, Mock
import zlib

from fs_capture import core as c
from fs_capture import preview as p
from fs_capture.report import capture_check
import test_pipeline
import test_reliability


def png(width=128, height=128):
    def chunk(kind, data):
        return struct.pack('>I', len(data))+kind+data+struct.pack('>I', zlib.crc32(kind+data))
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR', struct.pack('>IIBBBBB',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\0\0\0\xff'*width)*height))+chunk(b'IEND',b'')


class PoseTests(unittest.TestCase):
    def test_translation_is_not_camera_centre(self):
        self.assertEqual(p.pose([1,0,0,0],[1,2,3]), {'position':[-1,-2,-3], 'right':[1,0,0], 'up':[0,-1,0], 'forward':[0,0,1]})

    def test_rotated_camera_and_roundtrip(self):
        q=[2**-.5,0,2**-.5,0]
        view=p.pose(q,[1,2,3])
        for actual,expected in zip(view['position'],[3,-2,-1]):self.assertAlmostEqual(actual,expected)
        # Point three units in front must transform to camera z=3, x=y=0.
        point=[x+3*f for x,f in zip(view['position'],view['forward'])]
        for actual,expected in zip([point[2]+1,point[1]+2,-point[0]+3],[0,0,3]):self.assertAlmostEqual(actual,expected)

    def test_invalid_quaternion(self):
        for q in ([0,0,0,0],[1,1,0,0],[float('nan'),0,0,0]):
            with self.assertRaises(c.CaptureError):p.pose(q,[0,0,0])

    @unittest.skipUnless(shutil.which('node'),'Node is needed for projection tests')
    def test_actual_browser_projection_module(self):
        subprocess.run(['node','--test',str(Path(__file__).with_name('projection.test.mjs'))],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)

    @unittest.skipUnless(shutil.which('node'),'Node is needed for UI contract tests')
    def test_ui_context_contract_without_browser(self):
        subprocess.run(['node','--test',str(Path(__file__).with_name('preview-ui.test.mjs'))],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)


class CaptureHistoryTests(unittest.TestCase):
    setUp=test_pipeline.PipelineTests.setUp
    tearDown=test_pipeline.PipelineTests.tearDown
    fake_extract=test_pipeline.PipelineTests.fake_extract
    extract=test_pipeline.PipelineTests.extract

    def test_pre_generation_and_full_history(self):
        first=capture_check(self.job,'entrance','NOT_TESTED','before extraction','tester')
        self.assertEqual(first['items'][0]['artifacts'],{})
        self.extract()
        capture_check(self.job,'entrance','NEEDS_CAPTURE','sparse','tester',['extract:images/cam0/00000.jpg'])
        before=c.read(self.job/'capture-qa.json')['items'][0]
        after=capture_check(self.job,'entrance','CAPTURED','observed','tester')['items'][0]
        previous=after['history'][-1]
        for key in ('configSha256','artifacts','references','id','label','status','reviewer','at'):
            self.assertEqual(previous[key],before[key])
        self.assertEqual(after['history'][0]['artifacts'],{})

    def test_changed_config_rejected_without_record_mutation(self):
        capture_check(self.job,'entrance','NOT_TESTED','before','tester')
        original=(self.job/'capture-qa.json').read_bytes()
        with (self.job/'job.json').open('a') as f:f.write('\n')
        with self.assertRaises(c.CaptureError):capture_check(self.job,'entrance','CAPTURED','changed','tester')
        self.assertEqual(original,(self.job/'capture-qa.json').read_bytes())

    def test_changed_artifact_and_unknown_reference_rejected(self):
        self.extract()
        with self.assertRaises(c.CaptureError):capture_check(self.job,'entrance','CAPTURED','test','tester',['extract:missing'])
        output=c.successful(c.read(self.job/'state.json'),'extract')
        (output/'images/cam0/00000.jpg').write_bytes(b'changed')
        with self.assertRaises(c.CaptureError):capture_check(self.job,'entrance','CAPTURED','test','tester')


class PreviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name).resolve()
        self.job=self.root/'job';self.job.mkdir()
        self.config={'settings':{'iterations':1,'maskModel':'synthetic-mask-model'}}
        c.write(self.job/'job.json',self.config)
        self.state={'configSha256':c.digest(self.job/'job.json'),'stages':{},'reviews':{}}
        self.outputs={s:self.job/s for s in ('extract','mask','sfm','train')}
        for folder in self.outputs.values():folder.mkdir()
        frames=[]
        for i in range(3):
            image=f'images/cam0/{i*10:05d}.jpg';mask=image.replace('images/','masks/').replace('.jpg','.png')
            for s,name in [('extract',image),('mask',mask)]:
                path=self.outputs[s]/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(png())
            frames.append({'frameIndex':i*10,'images':[image],'approximateSeconds':i/3})
        c.write(self.outputs['extract']/'source-index.json',{'frames':frames})
        test_reliability.model(self.outputs['sfm'])
        for s in ('extract','mask'):
            self.state['stages'][s]={'status':'succeeded','output':str(self.outputs[s]),'artifacts':c.fingerprint(self.outputs[s])}
        stats=c.inspect_artifacts('sfm',self.outputs['sfm'],self.config,self.state,write_metadata=True)
        train=self.outputs['train']/'training';train.mkdir()
        c.write(train/'scene_transform.json',{'train_from_world':{'matrix_4x4':p.IDENTITY}})
        c.write(train/'config.json',{'primitive':'3dgs'})
        ckpt=train/'step-000000001.ckpt';ckpt.mkdir();test_pipeline.gaussian(ckpt/'splat.ply')
        for s in ('sfm','train'):
            self.state['stages'][s]={'status':'succeeded','output':str(self.outputs[s]),'artifacts':c.fingerprint(self.outputs[s])}
        self.state['stages']['sfm']['validation']=stats
        for s in ('extract','mask','sfm'):
            self.state['reviews'][s]={'passed':True,'artifacts':self.state['stages'][s]['artifacts']}
        c.write(self.job/'state.json',self.state)
        # Runtime fixture only tests provenance and packaging; never used for rendering.
        self.runtime=self.root/'runtime';self.runtime.mkdir();(self.runtime/'LICENSE').write_text('synthetic runtime')
        self.assets=self.root/'assets';shutil.copytree(p.ASSETS,self.assets)
        c.write(self.assets/'runtime.json',{'commit':'synthetic','files':{'LICENSE':c.digest(self.runtime/'LICENSE')}})
        self.patch=patch.object(p,'ASSETS',self.assets);self.patch.start()

    def tearDown(self):
        self.patch.stop();self.tmp.cleanup()

    def prepare(self):return p.prepare(self.job,self.runtime,size=128)

    def record(self,folder):
        bundle=p.verify(folder);sidecar=c.read(folder/'cameras.json');v=sidecar['views'][0]
        return {'schemaVersion':1,'shDegree':0,'bundleId':bundle['id'],'category':'visual','status':'NOT_TESTED','label':'test','purpose':'synthetic test','reviewer':'test','reason':'no quality claim','at':'2026-09-25T00:00:00Z','device':'fixture',
                'view':{'cameraId':v['id'],'position':v['position'],'forward':v['forward'],'up':v['up'],'projection':sidecar['projection']},'screenshot':'data:image/png;base64,'+base64.b64encode(png()).decode()}

    def test_prepare_is_independent_and_preserves_originals(self):
        before={s:c.fingerprint(o) for s,o in self.outputs.items()}
        folder=self.prepare()
        self.assertEqual({s:c.fingerprint(o) for s,o in self.outputs.items()},before)
        self.assertEqual(c.read(folder/'scene.json')['visual'],'NOT_TESTED')
        self.assertEqual(c.read(folder/'cameras.json')['views'][0]['intrinsics']['model'],'OPENCV_FISHEYE')
        self.assertNotEqual((folder/'model.ply').stat().st_ino,(self.outputs['train']/'training/step-000000001.ckpt/splat.ply').stat().st_ino)
        self.assertNotIn(str(self.root), (folder/'scene.json').read_text())

    def test_modified_success_artifact_blocks_preview(self):
        (self.outputs['train']/'training/step-000000001.ckpt/splat.ply').write_bytes(b'bad')
        with self.assertRaises(c.CaptureError):self.prepare()

    def test_no_mask_job_previews_without_fake_mask(self):
        self.config['settings']['maskModel']=None
        c.write(self.job/'job.json',self.config)
        self.state['configSha256']=c.digest(self.job/'job.json')
        del self.state['stages']['mask'];del self.state['reviews']['mask']
        c.write(self.job/'state.json',self.state)
        folder=self.prepare()
        self.assertEqual(c.read(folder/'scene.json')['masking'],'NOT_APPLIED')
        self.assertTrue(all(v['maskUrl'] is None for v in c.read(folder/'cameras.json')['views']))
        self.assertFalse((folder/'masks').exists())

    def test_required_masks_missing_corrupt_or_unreviewed_are_rejected(self):
        mask=self.outputs['mask']/'masks/cam0/00000.png'
        original=mask.read_bytes()
        for bad in (None,b'broken'):
            if bad is None:mask.unlink()
            else:mask.write_bytes(bad)
            with self.assertRaises(c.CaptureError):self.prepare()
            mask.write_bytes(original)
        self.state['reviews']['mask']['passed']=False;c.write(self.job/'state.json',self.state)
        with self.assertRaises(c.CaptureError):self.prepare()

    def test_unreviewed_sfm_blocks_but_unreviewed_train_is_allowed(self):
        self.prepare()
        self.state['reviews']['sfm']['passed']=False;c.write(self.job/'state.json',self.state)
        with self.assertRaises(c.CaptureError):self.prepare()

    def test_unknown_transform_or_training_primitive_rejected(self):
        train=self.outputs['train']/'training'
        for filename,data in [('scene_transform.json',{'train_from_world':{'matrix_4x4':[]}}),('config.json',{'primitive':'3dgut'})]:
            original=(train/filename).read_bytes();c.write(train/filename,data)
            self.state['stages']['train']['artifacts']=c.fingerprint(self.outputs['train']);c.write(self.job/'state.json',self.state)
            with self.assertRaises(c.CaptureError):self.prepare()
            (train/filename).write_bytes(original)

    def test_runtime_mismatch_is_rejected(self):
        (self.runtime/'LICENSE').write_text('different')
        with self.assertRaises(c.CaptureError):self.prepare()

    def test_unsupported_camera_fails(self):
        path=self.root/'cameras.bin';path.write_bytes(struct.pack('<QiiQQ4d',1,1,2,10,10,1,5,5,0))
        with self.assertRaises(c.CaptureError):p.cameras(path)

    def test_import_retains_history_and_binds_evidence(self):
        folder=self.prepare();record=self.record(folder);source=self.root/'record.json';c.write(source,record)
        first=p.import_review(folder,source);record['status']='FAIL';c.write(source,record);second=p.import_review(folder,source)
        self.assertNotEqual(first['review'],second['review'])
        self.assertEqual(c.read(Path(first['review']))['status'],'NOT_TESTED')
        saved=c.read(Path(second['review']))
        self.assertEqual(saved['evidenceSha256'],c.digest(folder/saved['evidence']))
        self.assertEqual(p.verify(folder)['id'],record['bundleId'])

    def test_stale_qa_wrong_projection_and_bad_axes_rejected(self):
        folder=self.prepare();record=self.record(folder);source=self.root/'record.json'
        variants=[]
        for key,value in [('bundleId','stale'),('category','delivery'),('reviewer',''),('status','N/A')]:
            variant=copy.deepcopy(record);variant[key]=value;variants.append(variant)
        for key,value in [('projection',{}),('position',[float('nan'),0,0]),('forward',[0,0,2]),('up',[0,0,1])]:
            variant=copy.deepcopy(record);variant['view'][key]=value;variants.append(variant)
        for variant in variants:
            source.write_text(json.dumps(variant))
            with self.assertRaises(c.CaptureError):p.import_review(folder,source)
        self.assertFalse((folder/'reviews').exists())

    def test_truncated_or_wrong_size_screenshot_rejected(self):
        for raw in (png()[:-4], png(64,64), b'\x89PNG\r\n\x1a\n'):
            with self.assertRaises(c.CaptureError):p.check_png(raw,{'width':128,'height':128})

    def test_tampered_bundle_symlinks_and_traversal(self):
        folder=self.prepare();(folder/'model.ply').write_bytes(b'changed')
        with self.assertRaises(c.CaptureError):p.verify(folder)
        for name in ('../job.json','/etc/passwd'):
            with self.assertRaises(c.CaptureError):p.safe_file(folder,name)
        (folder/'linked').symlink_to(self.job/'job.json')
        with self.assertRaises(c.CaptureError):p.safe_file(folder,'linked')

    def request(self,folder,path,host='127.0.0.1:8768',origin=None):
        handler=p.handler(folder).__new__(p.handler(folder))
        handler.path=path;handler.headers={'Host':host};handler.server=SimpleNamespace(server_port=8768)
        if origin:handler.headers['Origin']=origin
        handler.wfile=io.BytesIO();handler.send_error=Mock();handler.send_response=Mock();handler.send_header=Mock();handler.end_headers=Mock()
        handler.do_GET();return handler

    def test_server_allowlist_host_origin_and_no_directory_listing(self):
        folder=self.prepare()
        self.assertIn(b'FS Capture',self.request(folder,'/').wfile.getvalue())
        for path,host,origin in [('/job.json','127.0.0.1:8768',None),('/../job.json','127.0.0.1:8768',None),('/','evil.test',None),('/','127.0.0.1:8768','https://evil.test')]:
            self.assertTrue(self.request(folder,path,host,origin).send_error.called)
        self.assertFalse(hasattr(p.handler(folder),'do_POST'))

    def test_modified_evidence_is_not_restored(self):
        folder=self.prepare();source=self.root/'record.json';c.write(source,self.record(folder));result=p.import_review(folder,source)
        saved=c.read(Path(result['review']));(folder/saved['evidence']).write_bytes(b'changed')
        self.assertTrue(self.request(folder,'/qa.json').send_error.called)
