"""Internal, standalone image report; never a delivery approval or public package."""
import copy
import base64
import html
import os
from urllib.parse import quote
from pathlib import Path
import time
import uuid

from . import core as c
from .media import thumbnail


def capture_check(job, label, status, note, reviewer, references=None, field_observation=None):
    root = Path(job).resolve()
    if not label.strip() or not note.strip() or not reviewer.strip() or status not in ('CAPTURED', 'NOT_CAPTURED', 'OCCLUDED', 'NEEDS_CAPTURE', 'NOT_TESTED'):
        raise c.CaptureError('確認箇所・担当者・理由と有効な状態が必要です。')
    with c.locked(root):
        state = c.read(root / 'state.json')
        if c.digest(root / 'job.json') != state['configSha256']:
            raise c.CaptureError('ジョブ設定が変更されています。')
        artifacts = {}
        for stage, record in state['stages'].items():
            if record['status'] == 'succeeded':
                checked_output(state, stage)
                artifacts[stage] = copy.deepcopy(record['artifacts'])
        references = references or []
        photos = []
        for reference in references:
            stage, separator, name = reference.partition(':')
            if stage == 'photo':
                photo = Path(name).resolve()
                if not photo.is_file() or photo.suffix.lower() not in ('.jpg', '.jpeg', '.png'):
                    raise c.CaptureError('Reference photo missing or unsupported')
                from .media import image_pixels
                image_pixels(photo, c.read(root/'job.json')['settings'].get('ffmpeg', 'ffmpeg'), c.read(root/'job.json')['settings'].get('ffprobe', 'ffprobe'))
                photos.append({'path': str(photo), 'sha256': c.digest(photo)})
                continue
            if not separator or name not in artifacts.get(stage, {}):
                raise c.CaptureError('確認対象の成果物が見つかりません: ' + reference)
        if field_observation is not None:
            from .field import load_plan
            plan = load_plan(root, c.read(root/'job.json'), state)
            if not plan or label not in [x['label'] for x in plan['items']]:
                raise c.CaptureError('Unknown field target')
        path = root / 'capture-qa.json'
        qa = c.read(path) if path.exists() else {'schemaVersion': 1, 'items': []}
        entry = next((x for x in qa['items'] if x['label'] == label), None)
        if entry is None:
            entry = {'id': uuid.uuid4().hex, 'label': label}
            qa['items'].append(entry)
        if 'status' in entry:
            previous = copy.deepcopy({k: v for k, v in entry.items() if k != 'history'})
            entry.setdefault('history', []).append(previous)
        entry.update(status=status, note=note, reviewer=reviewer, at=time.time())
        entry['referencePhotos'] = photos
        entry.pop('fieldObservation', None)
        if field_observation is not None:
            entry['fieldObservation'] = dict(field_observation, planSha256=c.digest(root/'field-plan.json'))
        # Tie this human observation to the verified config and available output snapshots.
        entry['configSha256'] = state['configSha256']
        entry['artifacts'] = artifacts
        entry['references'] = {'targetId': entry['id'], 'scope': 'input configuration and available stage snapshots', 'stages': list(artifacts), 'files': list(references)}
        c.write(path, qa)
        if (root/'field-plan.json').exists():
            from .field import render
            render(root, c.read(root/'field-plan.json'))
    return qa


def checked_output(state, stage):
    output = c.successful(state, stage)
    if not state['stages'][stage].get('artifacts') or c.fingerprint(output) != state['stages'][stage]['artifacts']:
        raise c.CaptureError(f'{stage}の成果物が成功時と一致しません。')
    return output


def report(job):
    root = Path(job).resolve()
    with c.locked(root):
        config, state = c.read(root / 'job.json'), c.read(root / 'state.json')
        if c.digest(root / 'job.json') != state['configSha256']:
            raise c.CaptureError('ジョブ設定が変更されています。')
        extracted = checked_output(state, 'extract')
        index = c.read(extracted / 'source-index.json')
        mask = checked_output(state, 'mask') if state['stages'].get('mask', {}).get('status') == 'succeeded' else None
        sfm = checked_output(state, 'sfm') if state['stages'].get('sfm', {}).get('status') == 'succeeded' else None
        registered, poses = set(), []
        if sfm:
            poses = c.read(sfm / 'cameras.json')['cameras']
            registered = {p['image'] for p in poses}
        folder = root / 'reports' / uuid.uuid4().hex[:12]
        folder.mkdir(parents=True)
        escaped = html.escape
        cards = []
        ffmpeg = config['settings'].get('ffmpeg', 'ffmpeg')
        mask_stats = {x['file']: x for x in c.read(mask / 'image-inspection.json')} if mask and (mask / 'image-inspection.json').exists() else {}
        for frame in index['frames']:
            for i, name in enumerate(frame['images']):
                rel = name.removeprefix('images/')
                image_uri = 'data:image/jpeg;base64,' + base64.b64encode(thumbnail(extracted / name, ffmpeg)).decode()
                mask_rel = 'masks/' + rel.removesuffix('.jpg') + '.png'
                mask_uri = ''
                if mask:
                    mask_uri = 'data:image/png;base64,' + base64.b64encode(thumbnail(mask / mask_rel, ffmpeg, mask=True)).decode()
                status = ('REGISTERED' if rel in registered else 'MISSING') if sfm else 'NOT_TESTED'
                info = mask_stats.get(mask_rel, {})
                fraction = info.get('excludedFraction')
                exclusion = '未検査' if fraction is None else f'{fraction:.1%}'
                warnings = ' / '.join(info.get('warnings', []))
                image_link = quote(os.path.relpath(extracted / name, folder))
                mask_link = f'<a href="{quote(os.path.relpath(mask / mask_rel, folder))}" target="_blank" rel="noopener">元マスクを開く</a>' if mask else ''
                cards.append(f'''<article><h3>{escaped(frame.get('captureId', 'single'))} / cam{i} / frame {frame['frameIndex']}</h3>
<p>撮影モード: {escaped(frame.get('mode', 'moving'))} · 素材内の近似時刻: {frame['approximateSeconds']:.3f}秒 · SfM: <strong>{status}</strong></p>
<div class="pair"><figure><img src="{image_uri}" alt="採用した元画像の縮小表示"><figcaption><a href="{image_link}" target="_blank" rel="noopener">抽出画像を原寸で開く</a></figcaption></figure>
<figure><canvas width="320" height="320" data-image="{image_uri}" data-mask="{mask_uri}"></canvas><figcaption>{'赤＝除外領域（マスク重ね表示）' if mask else 'マスク未適用'}</figcaption></figure></div>
<p>除外率: {exclusion} {escaped(warnings)} {mask_link}</p></article>''')
        qa_path = root / 'capture-qa.json'
        qa = c.read(qa_path) if qa_path.exists() else {'items': []}
        checks = ''.join(f"<tr><td>{escaped(x['label'])}</td><td>{escaped(x.get('status','NOT_TESTED'))}</td><td>{escaped(x.get('reviewer',''))}</td><td>{escaped(x.get('note',''))}</td></tr>" for x in qa['items'])
        summary = []
        for cap in config.get('captures', []):
            frames = cap['frames']
            summary.append(f"<li>{escaped(cap['captureId'])}: {cap['mode']}、採用フレーム {escaped(', '.join(map(str, frames)))}。選択外の区間は未評価。</li>")
        pose_rows = ''.join(f"<tr><td>{escaped(p['image'])}</td><td>{escaped(str(p['tvec']))}</td><td>{escaped(str(p['qvec']))}</td></tr>" for p in poses)
        stage_summary = ' / '.join(f'{name}: {entry["status"]}' for name, entry in state['stages'].items())
        title = 'FS Capture — 内部素材確認'
        field_link = '<p><a href="../../field-plan.html">現場の撮影計画・確認表</a></p>' if (root/'field-plan.json').exists() else ''
        document = f'''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title}</title><style>body{{font-family:system-ui,sans-serif;background:#f2f4f5;color:#18312e;max-width:1100px;margin:auto;padding:24px}}h1{{font-size:28px}}.notice{{padding:16px;background:#fff1d4;border-left:4px solid #b67a00}}article{{background:white;padding:18px;margin:20px 0;border-radius:12px}}.pair{{display:flex;flex-wrap:wrap;gap:20px}}figure{{margin:0}}img,canvas{{max-width:100%;width:320px;height:auto}}table{{border-collapse:collapse;width:100%;background:white}}td,th{{padding:8px;text-align:left;border-bottom:1px solid #ddd}}code{{word-break:break-all}}</style>
<h1>{title}</h1><p class="notice">内部確認用・機密画像を含む可能性あり。顧客納品用ではありません。生成成功・撮影済みは画質合格、匿名化、公開承認を意味しません。</p>
<p>ジョブ設定ハッシュ: <code>{state['configSha256']}</code></p>
<p>処理状態: {escaped(stage_summary)}</p><h2>撮影範囲</h2><ul>{''.join(summary)}</ul><p>固定撮影の時刻や枚数を独立視点数とみなしません。動画間の同時刻性は仮定せず、未選択区間の網羅性は判定しません。SfMのMISSINGは採用した画像が未登録、NOT_TESTEDはSfM未実施です。</p>
{field_link}<h2>担当者の確認箇所</h2><table><tr><th>箇所</th><th>状態</th><th>担当者</th><th>理由</th></tr>{checks}</table>
<p>状態はCLIのcapture-checkで記録してreportを再生成します。CAPTURED＝撮影済み、NEEDS_CAPTURE＝追加撮影が必要、NOT_TESTED＝未確認。</p>
<h2>採用画像とマスク</h2><p>取り込みマスク: 白＝保持、黒＝除外。反転がないか元画像と比較してください。寸法・デコード・除外率の検査だけでは内容の正しさを保証しません。</p>{''.join(cards)}
<details><summary>登録カメラの姿勢（未校正・world-to-camera）</summary><table><tr><th>画像</th><th>tvec</th><th>qvec wxyz</th></tr>{pose_rows}</table></details>
<script>for (const canvas of document.querySelectorAll('canvas')) {{const image=new Image();image.onload=()=>{{canvas.width=image.width;canvas.height=image.height;const ctx=canvas.getContext('2d');ctx.drawImage(image,0,0);if(!canvas.dataset.mask)return;const mask=new Image();mask.onload=()=>{{const tmp=document.createElement('canvas');tmp.width=canvas.width;tmp.height=canvas.height;const mc=tmp.getContext('2d');mc.drawImage(mask,0,0,tmp.width,tmp.height);const rgba=mc.getImageData(0,0,tmp.width,tmp.height);for(let i=0;i<rgba.data.length;i+=4){{const excluded=rgba.data[i]===0;rgba.data[i]=255;rgba.data[i+1]=30;rgba.data[i+2]=30;rgba.data[i+3]=excluded?115:0;}}mc.putImageData(rgba,0,0);ctx.drawImage(tmp,0,0);}};mask.src=canvas.dataset.mask;}};image.src=canvas.dataset.image;}}</script></html>'''
        (folder / 'index.html').write_text(document, encoding='utf-8')
        c.write(folder / 'report.json', {'internalOnly': True, 'createdAt': time.time(), 'configSha256': state['configSha256'],
                                       'sourceIndex': index, 'captureQA': qa, 'stageArtifacts': {k: v.get('artifacts') for k,v in state['stages'].items()},
                                       'viewQA': 'NOT_TESTED', 'deliveryApproval': 'NOT_TESTED'})
        return {'report': str(folder / 'index.html'), 'internalOnly': True}
