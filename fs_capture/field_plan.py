"""A pre-capture document and explicit, non-destructive association to a new job."""
import copy
import datetime
from pathlib import Path
import time
import uuid

from . import core as c
from . import field


def create_before(folder, case, site, equipment, purpose, date, revision, reviewer, targets, needs):
    values = (case, site, equipment, purpose, date, revision, reviewer)
    if any(not x.strip() for x in values) or not targets or len(targets) != len(needs) or len(set(targets)) != len(targets) or any(not x.strip() for x in targets+needs):
        raise c.CaptureError('撮影前計画には各対象と同じ数の --need（観測したい情報・合格条件）が必要です。')
    datetime.date.fromisoformat(date)
    folder = Path(folder).resolve()
    plan = {'schemaVersion': 2, 'kind': 'pre-capture', 'planId': uuid.uuid4().hex,
            'internalOnly': True, 'createdAt': time.time(),
            'metadata': dict(zip(('caseId','siteId','equipmentId','purpose','plannedCaptureDate','revision','reviewer'), values)),
            'items': [{'id': uuid.uuid4().hex, 'label': label, 'requirement': need, 'visibility': 'UNOBSERVED', 'publication': 'NOT_TESTED', 'references': []} for label,need in zip(targets, needs)],
            'handling': {'persons':'NOT_TESTED','signs':'NOT_TESTED','confidential':'NOT_TESTED','accessPermission':'NOT_TESTED'},
            'evaluation': [], 'history': []}
    folder.mkdir(parents=True, exist_ok=False)
    c.write(folder/'field-plan.json', plan)
    # Seal the creation version. Later linking checks this instead of silently adopting edits.
    c.write(folder/'plan-origin.json', {'schemaVersion':1,'sha256':c.digest(folder/'field-plan.json')})
    field.render(folder, plan)
    return plan


def read_before(folder):
    path = folder/'field-plan.json'
    plan = c.read(path)
    if plan.get('schemaVersion') != 2 or plan.get('kind') != 'pre-capture' or not plan.get('planId'):
        raise c.CaptureError('撮影前計画を指定してください。')
    if c.digest(path) != c.read(folder/'plan-origin.json')['sha256']:
        raise c.CaptureError('当初の撮影計画が変更されています。元の版を保全してください。')
    return plan


def link(folder, job, case, site, equipment, reviewer, note):
    folder, root = Path(folder).resolve(), Path(job).resolve()
    if folder == root or not reviewer.strip() or not note.strip():
        raise c.CaptureError('別の新規ジョブ、関連付け担当者、確認理由が必要です。')
    with c.locked(folder), c.locked(root):
        original = read_before(folder)
        expected = dict(caseId=case,siteId=site,equipmentId=equipment)
        if any(original['metadata'][key] != value for key,value in expected.items()):
            raise c.CaptureError('案件・現場・設備が当初の計画と一致しません。')
        root, config, state = field.context(root)
        if state['stages'] or (root/'field-plan.json').exists() or (root/'capture-plan-source.json').exists():
            raise c.CaptureError('処理前・計画未登録の新規ジョブへ関連付けてください。既存関連付けは上書きしません。')
        c.verify_sources(config)
        raw = (folder/'field-plan.json').read_bytes()
        plan = copy.deepcopy(original)
        plan.update(kind='job-linked', configSha256=state['configSha256'],
                    conditions={'captures':config.get('captures',[]),'settings':config['settings'],'maskSource':config.get('maskSource')},
                    origin={'planId':original['planId'],'sha256':c.digest(folder/'field-plan.json'),
                            'snapshot':'capture-plan-source.json','linkedAt':time.time(),'reviewer':reviewer,'note':note})
        # Independent creation snapshot; no writable link to the original plan.
        (root/'capture-plan-source.json').write_bytes(raw)
        c.write(root/'field-plan.json', plan)
        field.render(root, plan)
        field.write_task_template(root)
    return plan


def report(folder):
    root = Path(folder).resolve()
    with c.locked(root):
        plan = c.read(root/'field-plan.json')
        if plan.get('kind') == 'pre-capture':
            plan = read_before(root)
        else:
            root, config, state = field.context(root)
            plan = field.load_plan(root, config, state)
        evidence = field.render(root, plan)
    return {'report':str(root/'field-plan.html'),'evidence':evidence,'internalOnly':True}
