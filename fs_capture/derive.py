"""Bounded train-only derivation: verified independent copies, never parent edits."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import time
import uuid

from . import core as c
from .report import checked_output


def object_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,allow_nan=False).encode()).hexdigest()


def dependency_key(config, state, stage):
    # Conservative upstream contract. Only iterations may change in this version.
    settings={k:v for k,v in config['settings'].items() if k!='iterations'}
    upstream=c.STAGES[:c.STAGES.index(stage)]
    return object_hash({'contract':'train-only-v1','stage':stage,'schema':config['schemaVersion'],
                        'source':config.get('source'),'captures':config.get('captures'),
                        'settings':settings,'maskSource':config.get('maskSource'),
                        'engine':state.get('engine'),'decoder':state.get('decoder'),
                        'upstream':{s:state['stages'][s]['artifacts'] for s in upstream if s in state['stages']}})


def derive(parent, job, iterations):
    parent,job=Path(parent).resolve(),Path(job).resolve()
    if type(iterations) is not int or iterations<1:
        raise c.CaptureError('iterationsは正の整数が必要です。')
    if job.is_relative_to(parent) or parent.is_relative_to(job):
        raise c.CaptureError('親ジョブと派生ジョブは独立したディレクトリにしてください。')
    with c.locked(parent):
        config,state=c.read(parent/'job.json'),c.read(parent/'state.json')
        config_sha=c.digest(parent/'job.json');state_sha=c.digest(parent/'state.json')
        if config_sha!=state['configSha256'] or config.get('schemaVersion') not in (1,2):
            raise c.CaptureError('親ジョブの設定・schemaが不正です。')
        if iterations==config['settings']['iterations']:
            raise c.CaptureError('学習反復数が同じです。変更する値を指定してください。')
        c.verify_sources(config)
        s=config['settings']
        if s.get('maskModel') and c.digest(s['maskModel'])!=s['maskModelSha256']:
            raise c.CaptureError('マスクモデルが変更されています。')
        if not state.get('engine'):
            raise c.CaptureError('生成エンジンの識別記録がありません。')
        for key in ('engine','decoder'):
            if key in state and c.digest(state[key]['path'])!=state[key]['sha256']:
                raise c.CaptureError('実行ファイルが変更されています。')
        stages=['extract',*(['mask'] if c.has_masks(config) else []),'sfm']
        if not c.has_masks(config) and 'mask' in state['stages']:
            raise c.CaptureError('マスク設定と段階が一致しません。')
        outputs={}
        for stage in stages:
            folder=checked_output(state,stage)
            if not folder.resolve().is_relative_to(parent) or any(p.is_symlink() for p in [folder,*folder.parents]):
                raise c.CaptureError('親ジョブ外またはリンクの成果物は再利用できません。')
            c.reviewed(state,stage)
            c.inspect_artifacts(stage,folder,config,state)
            outputs[stage]=folder
        child=copy.deepcopy(config);child['createdAt']=time.time();child['settings']['iterations']=iterations
        origin={'schemaVersion':1,'kind':'train-only','parent':str(parent),'parentConfigSha256':config_sha,
                'parentStateSha256':state_sha,'changed':{'iterations':{'from':s['iterations'],'to':iterations}},
                'reused':stages,'visualQA':'NOT_TESTED','train':'NOT_RUN'}
        child['derivation']=origin
        for stage in stages:
            if dependency_key(config,state,stage)!=dependency_key(child,state,stage):
                raise c.CaptureError('上流の依存条件が一致しません。')
        job.mkdir(parents=True,exist_ok=False)
        new={'status':'deriving','stages':{},'reviews':{},**{k:copy.deepcopy(state[k]) for k in ('engine','decoder') if k in state}}
        try:
            c.write(job/'job.json',child);new['configSha256']=c.digest(job/'job.json')
            c.write(job/'state.json',new)
            shutil.copyfile(parent/'probe.json',job/'probe.json')
            for stage in stages:
                attempt=job/'attempts'/f'{stage}-reused-{uuid.uuid4().hex[:12]}'
                output=attempt/'output';shutil.copytree(outputs[stage],output)
                snapshot=state['stages'][stage]['artifacts']
                if c.fingerprint(output)!=snapshot:
                    raise c.CaptureError('再利用コピー中に成果物が変更されました。')
                record={'status':'succeeded','phase':'complete','execution':'reused_not_executed',
                        'output':str(output),'attempt':str(attempt),'reusedAt':time.time(),
                        'artifacts':copy.deepcopy(snapshot),'dependencyKey':dependency_key(config,state,stage),
                        'reusedFrom':{'job':str(parent),'attempt':state['stages'][stage]['attempt'],
                                      'configSha256':config_sha,'stateSha256':state_sha}}
                new['stages'][stage]=record
                record['validation']=c.inspect_artifacts(stage,output,child,new)
                entry=copy.deepcopy(state['reviews'][stage])
                entry['inheritedFrom']=record['reusedFrom'];new['reviews'][stage]=entry
                c.write(attempt/'result.json',record)
            # A second read catches concurrent edits outside the application lock.
            if c.digest(parent/'job.json')!=config_sha or c.digest(parent/'state.json')!=state_sha:
                raise c.CaptureError('コピー中に親ジョブが変更されました。')
            for stage in stages:checked_output(state,stage);c.reviewed(new,stage)
            new['status']='ready_for_train'
        except BaseException as exc:
            new.update(status='derivation_failed',error=str(exc))
            raise
        finally:c.write(job/'state.json',new)
        return {'job':str(job),'reused':stages,'executed':[],'next':'run JOB train','visualQA':'NOT_TESTED'}
