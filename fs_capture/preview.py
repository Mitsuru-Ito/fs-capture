"""Local, immutable preview snapshots. No training or delivery approval is implied."""
import base64
import hashlib
import json
import math
import mimetypes
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import struct
import zlib
import time
from urllib.parse import urlsplit
import uuid

from . import core as c
from .report import checked_output
from .derive import object_hash

ASSETS = Path(__file__).with_name('preview_assets')
IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
MODELS = {0: 'SIMPLE_PINHOLE', 1: 'PINHOLE', 5: 'OPENCV_FISHEYE'}


def pose(q, t):
    """COLMAP world-to-camera wxyz -> native-world camera centre and GL axes."""
    if len(q) != 4 or len(t) != 3 or not all(math.isfinite(x) for x in (*q, *t)):
        raise c.CaptureError('Invalid pose')
    n = math.sqrt(sum(x*x for x in q))
    if abs(n - 1) > 1e-3:
        raise c.CaptureError('Non-unit camera quaternion')
    w, x, y, z = [v/n for v in q]
    r = [[1-2*(y*y+z*z), 2*(x*y-w*z), 2*(x*z+w*y)],
         [2*(x*y+w*z), 1-2*(x*x+z*z), 2*(y*z-w*x)],
         [2*(x*z-w*y), 2*(y*z+w*x), 1-2*(x*x+y*y)]]
    return {'position': [-sum(r[j][i]*t[j] for j in range(3)) for i in range(3)],
            'right': r[0], 'up': [-v for v in r[1]], 'forward': r[2]}


def cameras(path):
    result = {}
    with path.open('rb') as f:
        reader = c.BinaryReader(f)
        for _ in range(reader.count(40, nonempty=True)):
            cid, model, width, height = reader.unpack('<iiQQ')
            if model not in MODELS:
                raise c.CaptureError(f'Preview projection unsupported: camera model {model}')
            params = reader.unpack('<' + 'd' * c.CAMERA_PARAMETERS[model])
            if cid in result or not width or not height or not all(math.isfinite(x) for x in params) or min(params[:1 if model == 0 else 2]) <= 0:
                raise c.CaptureError('Invalid camera intrinsics')
            result[cid] = {'cameraId': cid, 'modelId': model, 'model': MODELS[model],
                           'width': width, 'height': height, 'params': list(params)}
        reader.end()
    return result


def safe_file(root, name):
    relative = Path(name)
    if relative.is_absolute() or '..' in relative.parts or '\\' in name:
        raise c.CaptureError('Unsafe preview path')
    path = root / relative
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent) or not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise c.CaptureError('Missing or linked preview file')
    return path


def prepare(job, runtime, fov=90, size=768):
    if not 20 <= fov <= 120 or not 128 <= size <= 1536:
        raise c.CaptureError('Review FOV must be 20–120 degrees; resolution 128–1536')
    root, runtime = Path(job).resolve(), Path(runtime).resolve()
    pin = c.read(ASSETS / 'runtime.json')
    for name, expected in pin['files'].items():
        if c.digest(safe_file(runtime, name)) != expected:
            raise c.CaptureError(f'Pinned renderer differs: {name}')
    with c.locked(root):
        config, state = c.read(root/'job.json'), c.read(root/'state.json')
        if c.digest(root/'job.json') != state['configSha256']:
            raise c.CaptureError('ジョブ設定が変更されています。')
        masked = c.has_masks(config)
        required = ('extract', 'mask', 'sfm') if masked else ('extract', 'sfm')
        if not masked and state['stages'].get('mask', {}).get('status') == 'succeeded':
            raise c.CaptureError('Mask output conflicts with unmasked job configuration')
        outputs = {s: checked_output(state, s) for s in (*required, 'train')}
        for s in required:
            c.reviewed(state, s)
        c.inspect_artifacts('sfm', outputs['sfm'], config, state)
        c.inspect_artifacts('train', outputs['train'], config, state)
        train = outputs['train']/'training'
        transform = c.read(train/'scene_transform.json')
        # Fail closed until nonidentity training transforms have dedicated tests.
        if transform.get('train_from_world', {}).get('matrix_4x4') != IDENTITY:
            raise c.CaptureError('Nonidentity training transform is not supported by this preview version')
        training = c.read(train/'config.json')
        # Spirula config is flat in the pinned version.
        primitive = training.get('primitive')
        if primitive is None:
            primitive = training.get('config', {}).get('primitive')
        if training.get('use_camera_optimizer') or training.get('warp_to_pinhole') or training.get('warp_spherical_to_pinhole'):
            raise c.CaptureError('Optimized/warped training cameras require a verified adapter')
        if any(training.get(k) is not None for k in ('splat_color_is_linear', 'splat_color_transfer', 'splat_color_gamut')):
            raise c.CaptureError('Explicit training color conversion is not supported by this preview')
        if primitive != '3dgs':
            raise c.CaptureError(f'Unsupported training primitive: {primitive}')
        sparse = outputs['sfm']/state['stages']['sfm']['validation']['model']
        intrinsics = cameras(sparse/'cameras.bin')
        poses = c.read_poses(sparse/'images.bin')
        index = c.read(outputs['extract']/'source-index.json')
        sources = {name.removeprefix('images/'): dict(frame, image=name)
                   for frame in index['frames'] for name in frame['images']}
        ply = train/f"step-{config['settings']['iterations']:09d}.ckpt"/'splat.ply'
        folder = root/'previews'/uuid.uuid4().hex[:12]
        folder.mkdir(parents=True)
        def add(source, name):
            target = folder/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
            return name
        add(ply, 'model.ply')
        for name in pin['files']:
            add(safe_file(runtime, name), 'runtime/'+name)
        for name in ('index.html', 'app.js', 'projection.mjs', 'style.css'):
            add(ASSETS/name, name)
        views = []
        for i, p in enumerate(sorted(poses, key=lambda p: p['image'])):
            source = sources[p['image']]
            mask_name = str(Path(source['image'].replace('images/', 'masks/', 1)).with_suffix('.png'))
            view = dict(p, **pose(p['qvec'], p['tvec']), intrinsics=intrinsics[p['cameraId']],
                        source={k: source[k] for k in ('captureId', 'mode', 'frameIndex', 'approximateSeconds') if k in source})
            view['source']['cameraId'] = Path(p['image']).parent.name
            view['imageUrl'] = add(safe_file(outputs['extract'], source['image']), f'images/{i}.jpg')
            view['maskUrl'] = add(safe_file(outputs['mask'], mask_name), f'masks/{i}.png') if masked else None
            views.append(view)
        sidecar = {'schemaVersion': 1, 'units': 'arbitrary units', 'worldFromReconstruction': IDENTITY,
                   'trainingTransformSha256': c.digest(train/'scene_transform.json'), 'views': views,
                   'projection': {'model': 'perspective', 'fovDegrees': fov, 'width': size, 'height': size,
                                  'reference': 'inverse mapping using calibrated COLMAP model; not original fisheye FOV'}}
        c.write(folder/'cameras.json', sidecar)
        with ply.open('rb') as stream:
            sh_properties = 0
            for line in stream:
                if line.startswith(b'property float f_rest_'):
                    sh_properties += 1
                if line.strip() == b'end_header':
                    break
        sh_degree = math.isqrt(sh_properties // 3 + 1) - 1
        if sh_properties != 3*((sh_degree+1)**2-1):
            raise c.CaptureError('Unexpected spherical harmonic layout')
        scene = {'schemaVersion': 1, 'internalOnly': True, 'configSha256': state['configSha256'],
                 'modelSha256': c.digest(ply), 'cameraSha256': c.digest(folder/'cameras.json'),
                 'renderer': {'commit': pin['commit'], 'runtimeFiles': pin['files'], 'primitive': '3dgs',
                              'sh': 'full degree; no silent fallback', 'shDegree': sh_degree, 'packing': 'float16 attributes / snorm8 SH',
                              'background': [0, 0, 0], 'exposure': 1, 'transfer': 0, 'gamut': 'Rec.709'},
                 'training': {'primitive': primitive, 'iterations': config['settings']['iterations'],
                              'configSha256': c.digest(train/'config.json')},
                 'coordinates': {'contract': 'identical-sfm-v1',
                                 'sfmArtifactsSha256': object_hash(state['stages']['sfm']['artifacts']),
                                 'extractArtifactsSha256': object_hash(state['stages']['extract']['artifacts']),
                                 'maskArtifactsSha256': object_hash(state['stages']['mask']['artifacts']) if masked else None},
                 'masking': 'APPLIED' if masked else 'NOT_APPLIED',
                 'process': 'PASS', 'visual': 'NOT_TESTED', 'navigation': 'NOT_TESTED',
                 'privacy': 'NOT_TESTED', 'delivery': 'NOT_TESTED'}
        c.write(folder/'scene.json', scene)
        files = c.fingerprint(folder)
        bundle = {'schemaVersion': 1, 'files': files}
        bundle['id'] = hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()
        c.write(folder/'bundle.json', bundle)
        verify(folder)
        return folder


def verify(folder):
    folder = Path(folder).resolve()
    bundle = c.read(folder/'bundle.json')
    actual_id = hashlib.sha256(json.dumps(bundle['files'], sort_keys=True).encode()).hexdigest()
    if bundle['id'] != actual_id:
        raise c.CaptureError('Preview manifest changed')
    for name, expected in bundle['files'].items():
        if c.digest(safe_file(folder, name)) != expected:
            raise c.CaptureError(f'Preview artifact changed: {name}')
    return bundle


def validate_record(record, bundle, sidecar, scene):
    comparison = scene.get('comparison')
    if record.get('schemaVersion') != (2 if comparison else 1) or record.get('bundleId') != bundle['id']:
        raise c.CaptureError('QA belongs to another model / renderer / projection')
    if comparison:
        candidate = next((x for x in comparison['candidates'] if x['id'] == record.get('candidateId')), None)
        if not candidate or any(record.get(k) != candidate[k] for k in ('modelSha256', 'sourceBundleId')):
            raise c.CaptureError('QA comparison candidate identity differs')
    if record.get('shDegree') != scene['renderer']['shDegree']:
        raise c.CaptureError('QA renderer SH degree differs')
    for key in ('reviewer', 'purpose', 'reason', 'label', 'device', 'at'):
        if not isinstance(record.get(key), str) or not record[key].strip() or len(record[key]) > 4000:
            raise c.CaptureError(f'QA requires {key}')
    if record.get('status') not in ('PASS', 'FAIL', 'NOT_TESTED', 'UNKNOWN') or record.get('category') != 'visual':
        raise c.CaptureError('This screen records only visual QA, not delivery approval')
    view = record.get('view', {})
    if view.get('projection') != sidecar['projection'] or view.get('cameraId') not in [p['id'] for p in sidecar['views']]:
        raise c.CaptureError('Unknown viewpoint projection or capture')
    for key in ('position', 'forward', 'up'):
        values = view.get(key, [])
        if len(values) != 3 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in values):
            raise c.CaptureError('Invalid viewpoint')
    for key in ('forward', 'up'):
        if abs(sum(x*x for x in view[key])-1) > 1e-5:
            raise c.CaptureError('Nonunit viewpoint axis')
    if abs(sum(x*y for x, y in zip(view['forward'], view['up']))) > 1e-5:
        raise c.CaptureError('Nonorthogonal viewpoint axes')


def compare(first, second, output, viewpoints_from=None):
    """Package two verified previews sharing exact reconstruction and display conditions."""
    first, second, output = [Path(p).resolve() for p in (first, second, output)]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in (first, second)):
        raise c.CaptureError('Comparison output must be independent of input previews')
    bundles = [verify(p) for p in (first, second)]
    scenes = [c.read(p/'scene.json') for p in (first, second)]
    a, b = scenes
    if a.get('comparison') or b.get('comparison'):
        raise c.CaptureError('Select two single-model previews')
    if not a.get('coordinates') or a['coordinates'].get('contract') != 'identical-sfm-v1':
        raise c.CaptureError('Recreate previews with verified SfM identity')
    for key in ('coordinates', 'cameraSha256', 'renderer', 'masking'):
        if a.get(key) != b.get(key):
            raise c.CaptureError(f'Not the same reconstruction / projection / renderer: {key}')
    for name in ('app.js', 'index.html', 'projection.mjs', 'style.css'):
        if any(x['files'].get(name) != c.digest(ASSETS/name) for x in bundles):
            raise c.CaptureError('Recreate previews with the current comparison UI')
    presets=[]
    if viewpoints_from:
        prior=Path(viewpoints_from).resolve();old=verify(prior);old_scene=c.read(prior/'scene.json')
        if old_scene['modelSha256']!=a['modelSha256'] or old_scene['cameraSha256']!=a['cameraSha256']:
            raise c.CaptureError('Baseline viewpoint records need the same A model and cameras')
        for path in sorted((prior/'reviews').glob('*.json')):
            record=c.read(path);validate_record(record,old,c.read(prior/'cameras.json'),old_scene)
            if c.digest(safe_file(prior,record['evidence']))!=record['evidenceSha256']:
                raise c.CaptureError('Baseline QA evidence changed')
            presets.append({'label':record['label'],'view':record['view'],'sourceReviewId':record['id'],
                            'sourceBundleId':old['id'],'previousStatus':record['status'],
                            'status':'NOT_TESTED'})
    output.mkdir(parents=True,exist_ok=False)
    for name in bundles[0]['files']:
        target=output/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copyfile(safe_file(first,name),target)
        if c.digest(target)!=bundles[0]['files'][name]:
            raise c.CaptureError('Preview changed while copying')
    shutil.copyfile(safe_file(second,'model.ply'),output/'candidate-b.ply')
    if c.digest(output/'candidate-b.ply')!=b['modelSha256']:
        raise c.CaptureError('Candidate model changed while copying')
    a['comparison']={'contract':'identical-sfm-and-display-v1','candidates':[
        {'id':ident,'url':url,'modelSha256':s['modelSha256'],'sourceBundleId':bundle['id'],
         'training':s['training'],'configSha256':s['configSha256'],'visual':'NOT_TESTED'}
        for ident,url,s,bundle in zip(('A','B'),('model.ply','candidate-b.ply'),scenes,bundles)]}
    c.write(output/'scene.json',a);c.write(output/'viewpoints.json',{'items':presets})
    files=c.fingerprint(output);bundle={'schemaVersion':1,'files':files,'id':object_hash(files)}
    c.write(output/'bundle.json',bundle);verify(output)
    return output


def check_png(raw, projection):
    if len(raw) > 12_000_000 or not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise c.CaptureError('Invalid screenshot format/size')
    at, compressed, header, ended = 8, bytearray(), None, False
    while at + 12 <= len(raw):
        n = struct.unpack('>I', raw[at:at+4])[0]
        kind, data = raw[at+4:at+8], raw[at+8:at+8+n]
        if at + n + 12 > len(raw) or zlib.crc32(kind+data) != struct.unpack('>I', raw[at+8+n:at+12+n])[0]:
            raise c.CaptureError('Corrupt screenshot PNG')
        if header is None and kind != b'IHDR':
            raise c.CaptureError('Missing PNG header')
        if kind == b'IHDR':
            if header is not None or n != 13:
                raise c.CaptureError('Invalid PNG header')
            header = struct.unpack('>IIBBBBB', data)
        elif kind == b'IDAT':
            compressed.extend(data)
        elif kind == b'IEND':
            ended = n == 0 and at + 12 == len(raw)
            break
        at += n + 12
    if not ended or header != (projection['width'], projection['height'], 8, 6, 0, 0, 0):
        raise c.CaptureError('Screenshot must be RGBA8 at the recorded resolution')
    expected = (projection['width']*4+1)*projection['height']
    try:
        decoder = zlib.decompressobj()
        pixels = decoder.decompress(compressed, expected+1)
        if len(pixels) != expected or not decoder.eof or decoder.unused_data:
            raise c.CaptureError('Invalid screenshot pixels')
    except zlib.error as exc:
        raise c.CaptureError('Invalid screenshot compression') from exc


def review_identity(record):
    """v1 canonical JSON content, including time, reviewer, candidate and PNG.

    Export attempts may differ; the observation describes a new confirmation.
    Legacy schema 1/2 use the entire unchanged content as their retry key.
    """
    observation, export = record.get('observationId'), record.get('exportId')
    if (observation is None) != (export is None):
        raise c.CaptureError('Both observationId and exportId are required')
    if observation is not None and any(not isinstance(x, str) or not x.strip() or len(x) > 128 for x in (observation, export)):
        raise c.CaptureError('Invalid observation/export identity')
    payload = {k: v for k, v in record.items() if k != 'exportId'}
    return {'rule': 'canonical-json-v1', 'observationId': observation, 'exportId': export,
            'payloadSha256': object_hash(payload)}


def import_review(folder, source):
    folder = Path(folder).resolve()
    with c.locked(folder):
        bundle = verify(folder)
        if Path(source).stat().st_size > 18_000_000:
            raise c.CaptureError('Review file too large')
        record = c.read(Path(source))
        validate_record(record, bundle, c.read(folder/'cameras.json'), c.read(folder/'scene.json'))
        if any(k in record for k in ('id','importedAt','evidence','evidenceSha256','context','importIdentity','exportAliases')):
            raise c.CaptureError('Import expects an export, not an already imported record')
        identity = review_identity(record)
        png = record.pop('screenshot', '')
        if not png.startswith('data:image/png;base64,'):
            raise c.CaptureError('Review screenshot is required')
        try:
            raw = base64.b64decode(png.split(',', 1)[1], validate=True)
        except ValueError as exc:
            raise c.CaptureError('Invalid screenshot') from exc
        check_png(raw, c.read(folder/'cameras.json')['projection'])
        target = folder/'reviews'
        target.mkdir(exist_ok=True)
        for path in sorted(target.glob('*.json')):
            previous = c.read(path)
            validate_record(previous, bundle, c.read(folder/'cameras.json'), c.read(folder/'scene.json'))
            # Old records are reconstructed read-only; no historical records are merged.
            evidence = safe_file(folder, previous['evidence'])
            if c.digest(evidence) != previous['evidenceSha256']:
                raise c.CaptureError('Existing review evidence changed')
            original = {k: v for k, v in previous.items() if k not in
                        ('id', 'importedAt', 'evidence', 'evidenceSha256', 'context', 'importIdentity', 'exportAliases')}
            original['screenshot'] = 'data:image/png;base64,' + base64.b64encode(evidence.read_bytes()).decode()
            prior = review_identity(original)
            if previous.get('importIdentity', prior) != prior:
                raise c.CaptureError('Existing review identity changed')
            shared = identity['observationId'] and identity['observationId'] == prior['observationId']
            shared_export = identity['exportId'] and identity['exportId'] in [prior['exportId'], *previous.get('exportAliases', [])]
            if shared or shared_export:
                if identity['payloadSha256'] != prior['payloadSha256']:
                    raise c.CaptureError('Review identity conflict: content differs')
            if shared or shared_export or identity == prior:
                if identity['exportId'] and identity['exportId'] != prior['exportId'] and identity['exportId'] not in previous.get('exportAliases', []):
                    previous.setdefault('exportAliases', []).append(identity['exportId'])
                    c.write(path, previous)
                return {'review': str(path), 'status': previous['status'], 'duplicate': True,
                        'scope': 'specified view and purpose only'}
        ident = uuid.uuid4().hex
        # The JSON is the commit marker, written atomically after the evidence.
        (target/f'{ident}.png').write_bytes(raw)
        record.update(id=ident, importIdentity=identity, importedAt=time.time(), evidence=f'reviews/{ident}.png',
                      evidenceSha256=hashlib.sha256(raw).hexdigest(), context=c.read(folder/'scene.json'))
        c.write(target/f'{ident}.json', record)
        return {'review': str(target/f'{ident}.json'), 'status': record['status'], 'scope': 'specified view and purpose only'}


def handler(folder):
    folder = Path(folder).resolve()
    bundle = verify(folder)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            host = f'127.0.0.1:{self.server.server_port}'
            if self.headers.get('Host') != host or self.headers.get('Origin', 'http://'+host) != 'http://'+host:
                self.send_error(403); return
            name = urlsplit(self.path).path.removeprefix('/') or 'index.html'
            try:
                if name == 'qa.json':
                    records = []
                    for p in sorted((folder/'reviews').glob('*.json')):
                        record = c.read(p)
                        validate_record(record, bundle, c.read(folder/'cameras.json'), c.read(folder/'scene.json'))
                        if c.digest(safe_file(folder, record['evidence'])) != record['evidenceSha256']:
                            raise c.CaptureError('QA evidence changed')
                        records.append(record)
                    data = json.dumps({'items': records}).encode()
                elif name == 'bundle.json':
                    data = json.dumps(bundle).encode()
                elif name in bundle['files']:
                    path = safe_file(folder, name)
                    if c.digest(path) != bundle['files'][name]:
                        raise c.CaptureError('Preview file changed; recreate preview')
                    data = path.read_bytes()
                else:
                    self.send_error(404); return
                self.send_response(200)
                self.send_header('Content-Type', mimetypes.guess_type(name)[0] or 'application/octet-stream')
                self.send_header('Content-Length', str(len(data)))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Content-Type-Options', 'nosniff')
                self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
                self.end_headers()
                self.wfile.write(data)
            except (OSError, ValueError, KeyError, c.CaptureError):
                self.send_error(409, 'Invalid preview or QA artifact')
    return Handler


def serve(folder, port=8768):
    with ThreadingHTTPServer(('127.0.0.1', port), handler(folder)) as server:
        print(f'Internal preview: http://127.0.0.1:{server.server_port}/', flush=True)
        print(f'Preview snapshot: {folder}', flush=True)
        server.serve_forever()
