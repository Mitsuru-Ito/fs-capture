"""Bounded capability checks; a tool version is never proof of GPU training."""
from pathlib import Path
import re
import subprocess
import struct
import tempfile
import time

from .core import (SPIRULA_VERSION, CaptureError, capture, digest, probe, read,
                   successful, tool, validate, verify_sources)

ERRORS = (CaptureError, OSError, ValueError, KeyError, ZeroDivisionError, struct.error, subprocess.TimeoutExpired)


def not_tested(reason):
    return {'status': 'NOT_TESTED', 'reason': reason}


def diagnose(spirula='spirula', ffprobe='ffprobe', ffmpeg='ffmpeg', decoder='spirula',
             source=None, check_gpu=False, job=None):
    result = {'schemaVersion': 1, 'checkedAt': time.time(),
              'requiredSpirulaVersion': SPIRULA_VERSION, 'decoder': decoder, 'tools': {}}
    for name, executable, flags in (('spirula', spirula, ['sfm', '--version']),
                                    ('ffprobe', ffprobe, ['-version']),
                                    ('ffmpeg', ffmpeg, ['-version'])):
        entry = {'status': 'FAILED', 'exists': False}
        result['tools'][name] = entry
        try:
            path = tool(executable)
            entry.update(exists=True, path=path)
            output = capture([path, *flags]).strip()
            if not output:
                raise CaptureError('版情報が空です。')
            compatible = name != 'spirula' or SPIRULA_VERSION in output
            entry.update(status='PASSED' if compatible else 'FAILED', version=output.splitlines()[0],
                         compatible=compatible)
        except ERRORS as exc:
            entry['error'] = str(exc)
    checks = result['checks'] = {
        'input': not_tested('--source未指定'),
        'decode': not_tested('実素材のデコード未実施'),
        'gpu': not_tested('--check-gpu未指定'),
        'training': not_tested('学習は自動実行しません。--jobで既存の成功記録を照合できます。'),
    }

    def required(name):
        entry = result['tools'][name]
        if entry['status'] != 'PASSED':
            raise CaptureError(f'{name}の導入・版確認に失敗しています。')
        return entry['path']

    if source:
        try:
            path = Path(source).resolve()
            if path.suffix.lower() != '.osv' or not path.is_file():
                raise CaptureError('既存のOSVを--sourceに指定してください。')
            _, fps = probe(path, required('ffprobe'))
            checks['input'] = {'status': 'PASSED', 'source': str(path), 'fps': fps,
                               'scope': '2映像トラックのメタデータ検査'}
        except ERRORS as exc:
            checks['input'] = {'status': 'FAILED', 'error': str(exc)}
        if checks['input']['status'] == 'PASSED' and decoder == 'ffmpeg':
            try:
                executable = required('ffmpeg')
                with tempfile.TemporaryDirectory(prefix='fs-capture-doctor-') as directory:
                    outputs = [Path(directory) / f'cam{i}.png' for i in range(2)]
                    argv = [executable, '-nostdin', '-hide_banner', '-v', 'error', '-xerror',
                            '-n', '-noautorotate', '-i', str(path)]
                    for i, output in enumerate(outputs):
                        argv += ['-map', f'0:V:{i}', '-frames:v', '1', '-update', '1', str(output)]
                    capture(argv)
                    if any(not p.is_file() or p.stat().st_size == 0 for p in outputs):
                        raise CaptureError('両魚眼の先頭フレームをデコードできませんでした。')
                checks['decode'] = {'status': 'PASSED', 'decoder': 'ffmpeg',
                                    'scope': '指定OSVの両魚眼の先頭1フレームのみ。全区間・同期・画質は未検証。'}
            except ERRORS as exc:
                checks['decode'] = {'status': 'FAILED', 'error': str(exc)}
        elif decoder == 'spirula':
            checks['decode'] = not_tested('Spirulaネイティブ抽出はdoctorでは実行しません。実ジョブのextractで確認してください。')

    if check_gpu:
        try:
            output = capture([required('spirula'), 'sam', 'devices', '--lang', 'en'])
            gpu = re.search(r'^\s*\d+\s+.+?\s+(?:discrete|integrated|virtual)\s+.+\s+ok\s*$', output, re.M)
            rows = re.search(r'^\s*\d+\s+.+?\s+(?:discrete|integrated|virtual|cpu|other)\s+', output, re.M)
            checks['gpu'] = {'status': 'PASSED' if gpu else ('FAILED' if rows else 'UNKNOWN'),
                             'evidence': output.strip(),
                             'scope': 'Spirula sam devicesのVulkan GPU列挙・使用可判定。学習実行の証明ではありません。'}
        except ERRORS as exc:
            checks['gpu'] = {'status': 'FAILED', 'error': str(exc)}

    if job:
        try:
            root = Path(job).resolve()
            if (root / '.lock').exists():
                raise CaptureError('ジョブがロック中のため学習記録を照合できません。')
            config, state = read(root / 'job.json'), read(root / 'state.json')
            if digest(root / 'job.json') != state['configSha256']:
                raise CaptureError('ジョブ設定が変更されています。')
            training = state['stages'].get('train', {})
            if training.get('status') != 'succeeded':
                checks['training'] = not_tested('指定ジョブに成功した学習記録がありません。')
            else:
                verify_sources(config)
                engine = state.get('engine', {})
                executable = required('spirula')
                if engine.get('path') != executable or engine.get('sha256') != digest(executable):
                    raise CaptureError('学習時と現在のSpirula実行ファイルが一致しません。')
                validation = validate('train', successful(state, 'train'), config, state)
                if validation != training.get('validation'):
                    raise CaptureError('学習成果物が成功記録と一致しません。')
                checks['training'] = {'status': 'PASSED', 'job': str(root), 'validation': validation,
                                      'scope': '既存ジョブの学習成功記録と最終PLYの照合。今回のGPU再実行・画質承認ではありません。'}
        except ERRORS as exc:
            checks['training'] = {'status': 'FAILED', 'error': str(exc)}

    required_tools = ['spirula', 'ffprobe'] + (['ffmpeg'] if decoder == 'ffmpeg' else [])
    okay = all(result['tools'][name]['status'] == 'PASSED' for name in required_tools)
    okay = okay and all(c['status'] not in ('FAILED', 'UNKNOWN') for c in checks.values())
    result['summary'] = 'CHECKS_PASSED' if okay else 'CHECKS_FAILED'
    result['note'] = '終了コード0も全段階の使用可能判定ではありません。NOT_TESTEDの項目とscopeを確認してください。'
    return result, 0 if okay else 1
