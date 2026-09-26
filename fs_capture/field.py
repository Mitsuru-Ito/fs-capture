"""Internal field preparation. No coverage, privacy or business-quality guarantee."""
import csv
import datetime
import html
import json
import math
from pathlib import Path
import shutil
import time

from . import core as c

STATUSES = ('CAPTURED', 'NOT_CAPTURED', 'OCCLUDED', 'NEEDS_CAPTURE', 'NOT_TESTED')
VISIBILITY = ('UNOBSERVED', 'OCCLUDED', 'PRIVACY_HIDDEN', 'RECONSTRUCTION_UNCERTAIN', 'OBSERVED')
DEFAULT_BUDGET = {'maxImages': 300, 'maxPairs': 44850, 'minFreeBytes': 1_073_741_824,
                  'reason': 'Provisional small-pilot cap, not measured engine capacity; explicitly revise for larger work.'}


def context(root):
    root = Path(root).resolve()
    config, state = c.read(root/'job.json'), c.read(root/'state.json')
    if c.digest(root/'job.json') != state['configSha256']:
        raise c.CaptureError('Job configuration changed')
    return root, config, state


def sources(config):
    return [x['source'] for x in config['captures']] if config.get('schemaVersion') == 2 else [config['source']]


def verify_evaluation(config, entries):
    training = sources(config)
    for entry in entries:
        path = Path(entry['path'])
        if not path.is_file() or c.digest(path) != entry['sha256']:
            raise c.CaptureError('Evaluation material missing or changed')
        if any(path.resolve() == Path(s['path']).resolve() or entry['sha256'] == s['sha256'] for s in training):
            raise c.CaptureError('Evaluation source overlaps training; reserve the entire capture including both lenses and adjacent frames')


def load_plan(root, config, state):
    path = root/'field-plan.json'
    if not path.exists():
        return None
    plan = c.read(path)
    if plan['configSha256'] != state['configSha256']:
        raise c.CaptureError('Field plan belongs to a different configuration')
    verify_evaluation(config, plan['evaluation'])
    return plan


def preflight(job, stage='sfm', copy_upstream=False):
    root, config, state = context(job)
    load_plan(root, config, state)
    verify_evaluation(config, config.get('evaluationSources', []))
    count, timestamps = 'UNKNOWN', 'UNKNOWN'
    if config.get('schemaVersion') == 2:
        timestamps = sum(len(x['frames']) for x in config['captures'])
        count = timestamps * 2
    elif state['stages'].get('extract', {}).get('status') == 'succeeded':
        from .report import checked_output
        index = c.read(checked_output(state, 'extract')/'source-index.json')
        timestamps = len(index['frames']); count = sum(len(x['images']) for x in index['frames'])
    elif (root/'probe.json').exists():
        streams = [s for s in c.read(root/'probe.json').get('streams', []) if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic', 0)]
        counts = [int(s['nb_frames']) for s in streams if str(s.get('nb_frames', '')).isdigit()]
        if len(counts) == 2 and counts[0] == counts[1]:
            timestamps = math.ceil(counts[0] / config['settings']['skip']); count = timestamps * 2
    try:
        free = shutil.disk_usage(root).free
    except OSError:
        free = 'UNKNOWN'
    # run streams videos directly; no original-video copy. Imported masks are copied.
    copy_bytes = 0
    if stage == 'mask' and config.get('maskSource'):
        masks = config['maskSource']; paths = [Path(masks['root'])/name for name in masks['artifacts']]
        copy_bytes = sum(p.stat().st_size for p in paths) if all(p.is_file() for p in paths) else 'UNKNOWN'
    copy_scope = 'this run stage only; excludes derive/preview copies'
    if copy_upstream:
        from .report import checked_output
        needed = ['extract'] + (['mask'] if c.has_masks(config) else []) + ['sfm']
        copy_bytes = 0
        for name in needed:
            if state['stages'].get(name, {}).get('status') != 'succeeded':
                copy_bytes = 'UNKNOWN'
                break
            folder = checked_output(state, name)
            copy_bytes += sum(p.stat().st_size for p in folder.rglob('*') if p.is_file())
        copy_scope = 'train-only derive upstream outputs; free space is on this job filesystem; preview copies excluded'
    policy = c.read(root/'budget.json') if (root/'budget.json').exists() else dict(DEFAULT_BUDGET)
    result = {'schemaVersion': 1, 'stage': stage, 'configSha256': state['configSha256'],
            'captures': len(sources(config)), 'timestamps': timestamps, 'images': count,
            'candidatePairs': count*(count-1)//2 if isinstance(count, int) else 'UNKNOWN',
            'pairMeaning': 'N(N-1)/2 theoretical candidates, not executed pairs or time',
            'matching': 'exhaustive (unchanged)', 'plannedCopyBytes': copy_bytes,
            'copyScope': copy_scope, 'freeBytes': free,
            'outputBytes': 'UNKNOWN', 'gpuSeconds': 'UNKNOWN',
            'validationTargets': ['source hashes', 'stage dependencies', 'image/mask readability', 'SfM/PLY structure'],
            'budget': policy}
    try:
        enforce(result, policy)
        result['decision'] = 'ALLOWED_UNKNOWN_EXTRACTION' if count == 'UNKNOWN' else 'WITHIN_PROVISIONAL_BUDGET'
    except c.CaptureError as exc:
        result.update(decision='BLOCKED', reason=str(exc))
    return result


def validate_budget(policy):
    if not isinstance(policy, dict) or not isinstance(policy.get('reason'), str) or not policy['reason'].strip():
        raise c.CaptureError('Budget decision needs a reason')
    for key in ('maxImages', 'maxPairs', 'minFreeBytes'):
        if type(policy.get(key)) is not int or policy[key] < (0 if key == 'minFreeBytes' else 1):
            raise c.CaptureError('Invalid budget: '+key)


def enforce(result, policy):
    validate_budget(policy)
    for metric, limit in (('images', 'maxImages'), ('candidatePairs', 'maxPairs')):
        if result[metric] == 'UNKNOWN' and result['stage'] == 'extract' and policy.get('allowUnknownExtraction') is True:
            continue
        if result[metric] == 'UNKNOWN' or result[metric] > policy[limit]:
            raise c.CaptureError(f'Budget blocked: {metric}={result[metric]}; inspect preflight and record an explicit budget decision')
    if result['freeBytes'] == 'UNKNOWN' or result['plannedCopyBytes'] == 'UNKNOWN' or result['freeBytes'] < policy['minFreeBytes'] + result['plannedCopyBytes']:
        raise c.CaptureError('Budget blocked: insufficient or unknown disk/copy size')


def set_budget(job, max_images, max_pairs, min_free_bytes, reason, allow_unknown_extraction=False):
    root, _, _ = context(job)
    policy = dict(maxImages=max_images, maxPairs=max_pairs, minFreeBytes=min_free_bytes, reason=reason, allowUnknownExtraction=allow_unknown_extraction)
    validate_budget(policy)
    with c.locked(root):
        path = root/'budget.json'
        history = []
        if path.exists():
            old = c.read(path); history = old.pop('history', []) + [old]
        policy.update(at=time.time(), history=history)
        c.write(path, policy)
    return policy


def render(root, plan):
    checks = c.read(root/'capture-qa.json')['items'] if (root/'capture-qa.json').exists() else []
    by_label = {x['label']: x for x in checks}
    rows = []
    for item in plan['items']:
        entry = by_label.get(item['label'], {})
        cells = [item['id'], item['label'], entry.get('status', 'NOT_TESTED'), entry.get('fieldObservation', {}).get('visibility', item['visibility']), entry.get('fieldObservation', {}).get('publication', item['publication']), entry.get('note', ''), ', '.join(entry.get('references', {}).get('files', []))]
        rows.append('<tr>'+''.join('<td>'+html.escape(str(x))+'</td>' for x in cells)+'</tr>')
    metadata = html.escape(json.dumps(plan['metadata'], ensure_ascii=False))
    text = '<!doctype html><meta charset="utf-8"><title>FS Capture 撮影確認表</title><style>body{font-family:system-ui;margin:2em}td,th{padding:.6em;border:1px solid #ccc}table{border-collapse:collapse}</style><h1>内部用 撮影確認表</h1>'
    text += '<p>安全な待機場所で確認してください。立入許可・現場の安全手順を代替しません。撮影済みは画質合格・公開承認ではありません。</p><p>'+metadata+'</p>'
    text += '<table><tr><th>ID</th><th>対象</th><th>撮影状況</th><th>見えない理由／観測</th><th>公開確認</th><th>理由</th><th>根拠</th></tr>'+''.join(rows)+'</table>'
    text += '<p>UNOBSERVED＝未観測 / OCCLUDED＝遮蔽 / PRIVACY_HIDDEN＝公開上の非表示 / RECONSTRUCTION_UNCERTAIN＝復元が不確か / OBSERVED＝観測（十分な品質の保証ではありません）。</p>'
    (root/'field-plan.html').write_text(text, encoding='utf-8')


def create_plan(job, case, site, equipment, purpose, date, revision, reviewer, targets):
    root, config, state = context(job)
    values = (case, site, equipment, purpose, date, revision, reviewer)
    if any(not isinstance(x, str) or not x.strip() for x in values) or not targets or any(not x.strip() for x in targets) or len(set(targets)) != len(targets):
        raise c.CaptureError('Required metadata and unique target labels are missing')
    datetime.date.fromisoformat(date)
    with c.locked(root):
        if (root/'field-plan.json').exists() or (root/'task-observations.csv').exists():
            raise c.CaptureError('Plan already exists; preserve the original plan')
        plan = {'schemaVersion': 1, 'internalOnly': True, 'configSha256': state['configSha256'],
                'metadata': dict(zip(('caseId','siteId','equipmentId','purpose','captureDate','revision','reviewer'), values)),
                'conditions': {'captures': config.get('captures', []), 'settings': config['settings'], 'maskSource': config.get('maskSource'), 'changeReason': 'Initial plan; actual acquisition not certified'},
                'handling': {'persons': 'NOT_TESTED', 'signs': 'NOT_TESTED', 'confidential': 'NOT_TESTED', 'accessPermission': 'NOT_TESTED'},
                'items': [{'id': str(i+1), 'label': label, 'visibility': 'UNOBSERVED', 'publication': 'NOT_TESTED', 'references': []} for i,label in enumerate(targets)],
                'evaluation': [], 'history': [], 'createdAt': time.time()}
        c.write(root/'field-plan.json', plan); render(root, plan)
        with (root/'task-observations.csv').open('x', encoding='utf-8', newline='') as stream:
            csv.writer(stream).writerow(['participantId','order','condition','equipment','task','correct','misidentification','unknownHandling','seconds','assistanceCount','failureReason','reviewer'])
    return plan


def add_evaluation(job, photo):
    root, config, state = context(job)
    with c.locked(root):
        plan = load_plan(root, config, state)
        if plan is None: raise c.CaptureError('Create a field plan first')
        path = Path(photo).resolve()
        entry = {'path': str(path), 'sha256': c.digest(path), 'role': 'evaluation-only', 'pose': 'UNKNOWN'}
        verify_evaluation(config, [entry])
        plan['evaluation'].append(entry)
        c.write(root/'field-plan.json', plan); render(root, plan)
    return entry


def check(job, label, status, note, reviewer, references, visibility='UNOBSERVED', publication='NOT_TESTED'):
    from .report import capture_check
    root, config, state = context(job)
    plan = load_plan(root, config, state)
    if not plan or label not in [x['label'] for x in plan['items']]:
        raise c.CaptureError('Unknown required target')
    if status not in STATUSES or visibility not in VISIBILITY or publication not in ('NOT_TESTED', 'RESTRICTED', 'APPROVED'):
        raise c.CaptureError('Invalid observation state')
    if status == 'CAPTURED' and not references:
        raise c.CaptureError('CAPTURED requires evidence references')
    # One atomic QA commit binds visibility, references, and judgment together.
    return capture_check(root, label, status, note, reviewer, references,
                         {'visibility': visibility, 'publication': publication})


def capture_references(job, capture_id, lens=None):
    """Select adopted images by capture/lens, without typing frame indices."""
    from .report import checked_output
    root, config, state = context(job)
    output = checked_output(state, 'extract')
    frames = c.read(output/'source-index.json')['frames']
    refs = ['extract:'+name for frame in frames if frame.get('captureId','single') == capture_id
            for name in frame['images'] if lens is None or lens in Path(name).parts]
    if not refs:
        raise c.CaptureError('No adopted images for this capture/lens')
    return refs


def draft_manifest(output, inputs, sample_fps=1, seconds=60, targets=None, evaluation=None):
    """Seconds-based moving-capture draft, never starts extraction/training."""
    if not inputs or any(not math.isfinite(x) or x <= 0 for x in (sample_fps, seconds)):
        raise c.CaptureError('Sources, positive duration and sample rate required')
    output = Path(output).resolve()
    if output.exists(): raise c.CaptureError('Manifest output already exists')
    captures = []
    for i, source in enumerate(inputs):
        path = Path(source).resolve()
        if path.suffix.lower() != '.osv': raise c.CaptureError('Original OSV required')
        metadata, fps = c.probe(path, c.tool('ffprobe'))
        if sample_fps > fps: raise c.CaptureError('Sample rate exceeds source rate')
        streams = [x for x in metadata['streams'] if x.get('codec_type') == 'video' and not x.get('disposition',{}).get('attached_pic',0)]
        limit = math.floor(seconds*fps)
        for stream in streams:
            if str(stream.get('nb_frames','')).isdigit(): limit = min(limit,int(stream['nb_frames']))
        stride = max(1,round(fps/sample_fps))
        if len(range(0,limit,stride)) < 3 or len(range(0,limit,stride)) > 10000:
            raise c.CaptureError('Moving selection requires 3–10000 timestamps; revise duration/rate')
        captures.append({'captureId': f'capture_{i+1:02d}', 'source': str(path), 'mode': 'moving',
                         'range': {'startFrame': 0, 'endFrameExclusive': limit, 'stride': stride}})
    result = {'schemaVersion':1, 'captures':captures, 'checklist': targets or [],
              'evaluationSources':[str(Path(x).resolve()) for x in evaluation or []]}
    # Reject copies as well as equal paths before writing a draft.
    config = {'schemaVersion':2,'captures':[{'source':{'path':x['source'],'sha256':c.digest(x['source'])}} for x in captures]}
    verify_evaluation(config,[{'path':x,'sha256':c.digest(x)} for x in result['evaluationSources']])
    c.write(output,result)
    return {'manifest':str(output),'status':'DRAFT','quality':'NOT_TESTED','timeBasis':'CFR frameIndex/sourceFps; approximate, not PTS'}


def log_work(job, activity, kind, minutes, operator, note):
    """Explicit operator input, never hidden tracking or inferred cost savings."""
    root, _, state = context(job)
    if kind not in ('human-active','machine-wait') or not math.isfinite(minutes) or minutes < 0 or any(not x.strip() for x in (activity,operator,note)):
        raise c.CaptureError('Activity, operator, note and finite nonnegative minutes required')
    with c.locked(root):
        path = root/'worklog.json'
        log = c.read(path) if path.exists() else {'schemaVersion':1,'items':[]}
        log['items'].append({'at':time.time(),'activity':activity,'kind':kind,'minutes':minutes,
                             'operator':operator,'note':note,'configSha256':state['configSha256']})
        c.write(path,log)
    return {'recorded':log['items'][-1], 'totalMinutes':{k:sum(x['minutes'] for x in log['items'] if x['kind']==k) for k in ('human-active','machine-wait')},
            'costAndBenefit':'NOT_TESTED; concurrent machine waits must not be added as human work'}
