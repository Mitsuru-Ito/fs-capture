"""Schema 2 manifest jobs: independent captures, paired lenses, explicit frames."""
from pathlib import Path
import re
import shutil
import time

from . import core as c
from .media import image_pixels


def positive(value, name):
    if type(value) is not int or value < 1:
        raise c.CaptureError(f'{name}は正の整数が必要です。')
    return value


def selected_frames(item):
    if ('frames' in item) == ('range' in item):
        raise c.CaptureError('framesまたはrangeを一つ指定してください。')
    if 'range' in item:
        r = item['range']
        start, end, stride = r['startFrame'], r['endFrameExclusive'], r['stride']
        if any(type(n) is not int for n in (start, end, stride)) or start < 0 or end <= start or stride < 1 or (end-start)//stride > 10000:
            raise c.CaptureError('採用フレーム区間が不正です。')
        frames = list(range(start, end, stride))
    else:
        frames = item['frames']
    if not isinstance(frames, list) or not frames or len(frames) > 10000 or any(type(n) is not int or n < 0 for n in frames) or frames != sorted(set(frames)):
        raise c.CaptureError('framesは重複のない昇順の非負整数（最大10000時刻）が必要です。')
    mode = item['mode']
    if mode not in ('fixed', 'moving') or (mode == 'fixed' and len(frames) != 1) or (mode == 'moving' and len(frames) < 3):
        raise c.CaptureError('fixedは1時刻、movingは3時刻以上を指定してください。')
    return frames


def index(config):
    frames = []
    for cap in config['captures']:
        for n in cap['frames']:
            frames.append({'captureId': cap['captureId'], 'mode': cap['mode'], 'frameIndex': n,
                           'approximateSeconds': n / cap['sourceFps'],
                           'images': [f"images/{cap['captureId']}/cam{i}/{n:05d}.jpg" for i in range(2)],
                           'cameraIds': ['cam0', 'cam1']})
    return {'timeBasis': 'per-capture frameIndex/sourceFps (approximate, not PTS); no cross-capture synchronization',
            'frames': frames}


def create_manifest(manifest, job, spirula='spirula', ffprobe='ffprobe', ffmpeg='ffmpeg'):
    manifest = Path(manifest).resolve()
    data = c.read(manifest)
    if not isinstance(data, dict):
        raise c.CaptureError('manifestはJSONオブジェクトで指定してください。')
    if data.get('schemaVersion') != 1 or not isinstance(data.get('captures'), list) or not data['captures']:
        raise c.CaptureError('manifest schemaVersion=1と空でないcapturesが必要です。')
    c.tool(ffmpeg)
    probe_tool = c.tool(ffprobe)
    captures, probes, ids, signatures, source_hashes = [], {}, set(), set(), set()
    for item in data['captures']:
        if not isinstance(item, dict) or not isinstance(item.get('source'), str) or not isinstance(item.get('captureId'), str):
            raise c.CaptureError('各captureにcaptureIdとsourceの文字列が必要です。')
        if item.get('role', 'training') != 'training':
            raise c.CaptureError('Evaluation material cannot be a training capture')
        cid = item['captureId']
        if not isinstance(cid, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', cid) or cid.casefold() in ids:
            raise c.CaptureError('captureIdは一意な英数字・_・-で指定してください（大文字小文字も区別しません）。')
        ids.add(cid.casefold())
        frames = selected_frames(item)
        source = (manifest.parent / item['source']).resolve()
        if source.suffix.lower() != '.osv' or not source.is_file() or not source.stat().st_size:
            raise c.CaptureError(f'素材がありません: {cid}')
        metadata, fps = c.probe(source, probe_tool)
        streams = [s for s in metadata['streams'] if s.get('codec_type') == 'video' and not s.get('disposition', {}).get('attached_pic', 0)]
        width, height = streams[0]['width'], streams[0]['height']
        signatures.add((width, height, round(fps, 3)))
        for stream in streams:
            count = stream.get('nb_frames')
            if count and count != 'N/A' and frames[-1] >= int(count):
                raise c.CaptureError(f'採用フレームが素材範囲外です: {cid}')
        sha = c.digest(source)
        if sha in source_hashes:
            raise c.CaptureError('同じ素材を別の独立撮影位置として重複登録できません。')
        source_hashes.add(sha)
        captures.append({'captureId': cid, 'mode': item['mode'], 'frames': frames, 'sourceFps': fps,
                         'width': width, 'height': height,
                         'source': {'path': str(source), 'sha256': sha, 'bytes': source.stat().st_size}})
        probes[cid] = metadata
    if len(signatures) != 1:
        raise c.CaptureError('今回の複数素材入力は解像度・フレームレートが同じ素材に限定します。')
    training = data.get('training', {})
    width = positive(data.get('imageWidth', captures[0]['width']), 'imageWidth')
    if width > captures[0]['width']:
        raise c.CaptureError('抽出画像の拡大は行いません。')
    height = round(width * captures[0]['height'] / captures[0]['width'])
    settings = {'decoder': 'ffmpeg', 'ffmpeg': ffmpeg, 'ffprobe': ffprobe, 'spirula': spirula,
                'spirulaVersion': c.SPIRULA_VERSION, 'maskModel': None, 'maskModelSha256': None,
                'iterations': positive(training.get('iterations', 30000), 'iterations'),
                'trainResolutionDivisor': positive(training.get('resolutionDivisor', 2), 'resolutionDivisor'),
                'capMax': positive(training.get('capMax', 200000), 'capMax'),
                'imageWidth': width, 'imageHeight': height}
    config = {'schemaVersion': 2, 'createdAt': time.time(), 'captures': captures, 'settings': settings,
              'manifestSha256': c.digest(manifest), 'maskSource': None}
    from .field import verify_evaluation
    evaluation = []
    for name in data.get('evaluationSources', []):
        path = (manifest.parent / name).resolve()
        evaluation.append({'path': str(path), 'sha256': c.digest(path), 'role': 'evaluation-only'})
    verify_evaluation(config, evaluation)
    if evaluation:
        config['evaluationSources'] = evaluation
    masking = data.get('masking')
    if masking is not None:
        if not isinstance(masking, dict) or not isinstance(masking.get('root'), str):
            raise c.CaptureError('masking.rootの文字列が必要です。')
        if masking.get('convention') != 'white-keep':
            raise c.CaptureError('convention="white-keep"を明示してください。黒保持・alpha方式の暗黙の変換は行いません。')
        categories = masking.get('categories', [])
        if not categories or not set(categories).issubset({'fisheye-boundary', 'person', 'moving-object', 'manual'}):
            raise c.CaptureError('masking.categoriesに除外対象を明示してください。')
        root = (manifest.parent / masking['root']).resolve()
        expected = {n.removeprefix('images/').removesuffix('.jpg') + '.png' for f in index(config)['frames'] for n in f['images']}
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*.png')}
        if expected != actual:
            raise c.CaptureError('マスクのcaptureId/cameraId/frameIndexが採用画像と一致しません。')
        hashes = {}
        for name in sorted(expected):
            path = root / name
            if path.is_symlink():
                raise c.CaptureError('マスク入力のシンボリックリンクは未対応です。')
            hashes[name] = c.digest(path)
        config['maskSource'] = {'root': str(root), 'convention': 'white-keep', 'categories': categories, 'artifacts': hashes}
    checklist = data.get('checklist', [])
    if not isinstance(checklist, list) or any(not isinstance(label, str) or not label.strip() for label in checklist):
        raise c.CaptureError('checklistは確認箇所名の配列です。')
    job = Path(job).resolve()
    job.mkdir(parents=True, exist_ok=False)
    c.write(job / 'job.json', config)
    c.write(job / 'probe.json', probes)
    c.write(job / 'state.json', {'status': 'created', 'stages': {}, 'reviews': {}, 'configSha256': c.digest(job / 'job.json')})
    c.write(job / 'capture-qa.json', {'schemaVersion': 1, 'items': [{'id': str(i+1), 'label': label, 'status': 'NOT_TESTED', 'note': ''} for i, label in enumerate(checklist)]})
    return config


def verify_inputs(config):
    for cap in config['captures']:
        if c.digest(cap['source']['path']) != cap['source']['sha256']:
            raise c.CaptureError(f"入力が変更されています: {cap['captureId']}")
    masks = config.get('maskSource')
    if masks:
        root = Path(masks['root'])
        actual = {p.relative_to(root).as_posix(): c.digest(p) for p in root.rglob('*.png')}
        if actual != masks['artifacts']:
            raise c.CaptureError('取り込み元マスクが変更されています。新規ジョブを作成してください。')


def commands(config, state, stage, output):
    s = config['settings']
    if stage == 'extract':
        result = []
        for cap in config['captures']:
            expression = '+'.join(f'eq(n\\,{n})' for n in cap['frames'])
            argv = [s['ffmpeg'], '-nostdin', '-hide_banner', '-v', 'warning', '-xerror', '-n', '-noautorotate', '-i', cap['source']['path']]
            for i in range(2):
                argv += ['-map', f'0:V:{i}', '-vf', f"select={expression},scale={s['imageWidth']}:{s['imageHeight']}",
                         '-frames:v', str(len(cap['frames'])), '-fps_mode', 'passthrough', '-q:v', '2', '-start_number', '0',
                         str(output / 'images' / cap['captureId'] / f'cam{i}' / 'decoded_%08d.jpg')]
            result.append(argv)
        return result
    if stage == 'mask':
        if not config.get('maskSource'):
            raise c.CaptureError('manifestに手修正マスクが登録されていません。')
        return []  # Internal copy, with provenance recorded by import_masks.
    if stage == 'sfm':
        images = c.successful(state, 'extract') / 'images'
        argv = [s['spirula'], 'sfm', 'auto', str(images), '-o', str(output), '--data-type', 'individual',
                '--quality', 'high', '--camera-model', 'opencv-fisheye', '--pairs', 'exhaustive']
        for cap in config['captures']:
            cid = cap['captureId']
            argv += ['--rig', f'dual-fisheye={cid}/cam0,{cid}/cam1']
        argv += ['--masks', str(c.successful(state, 'mask') / 'masks')] if config.get('maskSource') else ['--no-masks']
        return [argv]
    raise c.CaptureError('未対応のcollection段階です。')


def prepare(config, stage, output):
    if stage == 'extract':
        for cap in config['captures']:
            for cam in ('cam0', 'cam1'):
                (output / 'images' / cap['captureId'] / cam).mkdir(parents=True)
    if stage == 'mask':
        for name in config['maskSource']['artifacts']:
            target = output / 'masks' / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(config['maskSource']['root']) / name, target)
        c.write(output / 'import.json', config['maskSource'])


def normalize(config, stage, output):
    if stage == 'extract':
        for cap in config['captures']:
            for cam in ('cam0', 'cam1'):
                folder = output / 'images' / cap['captureId'] / cam
                files = sorted(folder.glob('decoded_*.jpg'))
                if [p.name for p in files] != [f'decoded_{i:08d}.jpg' for i in range(len(cap['frames']))]:
                    raise c.CaptureError('指定フレームの抽出が不足しています。')
                for path, n in zip(files, cap['frames']):
                    path.rename(path.with_name(f'{n:05d}.jpg'))


def inspect(config, state, stage, output, write_metadata=False):
    s = config['settings']
    source_index = index(config)
    expected = [n for frame in source_index['frames'] for n in frame['images']]
    stats = []
    names = [n if stage == 'extract' else n.replace('images/', 'masks/', 1).removesuffix('.jpg') + '.png' for n in expected]
    actual = [p.relative_to(output).as_posix() for p in (output / ('images' if stage == 'extract' else 'masks')).rglob('*') if p.is_file()]
    if set(actual) != set(names):
        raise c.CaptureError('成果物画像・マスクの集合が採用フレームと一致しません。')
    for name in names:
        info = image_pixels(output / name, s['ffmpeg'], s['ffprobe'], mask=stage == 'mask')
        if (info['width'], info['height']) != (s['imageWidth'], s['imageHeight']):
            raise c.CaptureError(f'画像・マスクの寸法不一致: {name}')
        stats.append({'file': name, **info})
    if stage == 'mask':
        if c.read(output / 'import.json') != config['maskSource']:
            raise c.CaptureError('マスク取り込み来歴が不一致です。')
        for name, sha in config['maskSource']['artifacts'].items():
            if c.digest(output / 'masks' / name) != sha:
                raise c.CaptureError('取り込んだマスクが元データと異なります。')
    if write_metadata:
        c.write(output / 'source-index.json', source_index)
        c.write(output / 'image-inspection.json', stats)
    elif c.read(output / 'source-index.json') != source_index or c.read(output / 'image-inspection.json') != stats:
        raise c.CaptureError('画像検査情報・フレーム対応が不一致です。')
    return {'captures': len(config['captures']), 'timestamps': len(source_index['frames']),
            'images' if stage == 'extract' else 'masks': len(names),
            'warnings': sum(len(x.get('warnings', [])) for x in stats)}
