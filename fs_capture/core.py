"""Spirula v2026.9.20 adapter. All external commands use argv, never a shell."""

import contextlib
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import signal
import struct
import subprocess
import time
import uuid
from fractions import Fraction

SPIRULA_VERSION = "2026.9.20"
STAGES = ("extract", "mask", "sfm", "train")


class CaptureError(Exception):
    pass


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    path = Path(path)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def fingerprint(folder):
    root = Path(folder)
    if not root.is_dir():
        raise CaptureError(f"成果物ディレクトリがありません: {root}")
    paths = sorted(root.rglob("*"))
    if any(p.is_symlink() for p in paths):
        raise CaptureError("成果物のシンボリックリンクは受け付けません。")
    result = {p.relative_to(root).as_posix(): digest(p) for p in paths if p.is_file()}
    if not result:
        raise CaptureError("成果物が空です。")
    return result


def tool(name):
    resolved = shutil.which(name)
    if not resolved:
        raise CaptureError(f"実行ファイルが見つかりません: {name}")
    return str(Path(resolved).resolve())


def capture(argv):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=60)
    if result.returncode:
        raise CaptureError(f"コマンド失敗 ({result.returncode}): {result.stderr[-3000:]}")
    return result.stdout


def probe(path, ffprobe):
    metadata = json.loads(capture([ffprobe, "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)]))
    streams = [s for s in metadata["streams"] if s.get("codec_type") == "video"
               and not s.get("disposition", {}).get("attached_pic", 0)]
    if len(streams) != 2:
        raise CaptureError("初期版は2映像トラックのOSV専用です。スティッチ済みMP4等は未対応です。")
    rates = [float(Fraction(s.get("avg_frame_rate", "0/1"))) for s in streams]
    if not all(math.isfinite(r) and r > 0 for r in rates) or abs(rates[0] - rates[1]) > 0.001:
        raise CaptureError("魚眼トラックのフレームレートが不明または不一致です。")
    if any(abs(float(Fraction(s.get("r_frame_rate", "0/1"))) - rates[i]) > 0.001 for i, s in enumerate(streams)):
        raise CaptureError("可変フレームレートの可能性があります。初期版では受け付けません。")
    if (streams[0]["width"], streams[0]["height"]) != (streams[1]["width"], streams[1]["height"]):
        raise CaptureError("魚眼トラックの解像度が不一致です。")
    starts = [float(s.get("start_time", 0)) for s in streams]
    if abs(starts[0] - starts[1]) > 0.001:
        raise CaptureError("魚眼トラックの開始時刻が不一致です。")
    return metadata, rates[0]


def create(source, job, fps=3, iterations=30000, spirula="spirula", ffprobe="ffprobe", mask_model=None,
           decoder="spirula", ffmpeg="ffmpeg"):
    source, job = Path(source).resolve(), Path(job).resolve()
    if source.suffix.lower() != ".osv" or not source.is_file() or source.stat().st_size == 0:
        raise CaptureError("空でないオリジナルOSVファイルを指定してください。")
    if not math.isfinite(fps) or fps <= 0 or iterations < 1:
        raise CaptureError("fpsとiterationsは正の値が必要です。")
    if decoder not in ("spirula", "ffmpeg"):
        raise CaptureError("decoderはspirulaまたはffmpegを指定してください。")
    metadata, source_fps = probe(source, tool(ffprobe))
    if fps > source_fps:
        raise CaptureError("抽出fpsが元動画のfpsを超えています。")
    model = Path(mask_model).resolve() if mask_model else None
    if model and not model.is_file():
        raise CaptureError("マスクモデルのファイルがありません。")
    config = {"schemaVersion": 1, "createdAt": time.time(),
              "source": {"path": str(source), "bytes": source.stat().st_size, "sha256": digest(source)},
              "settings": {"requestedFps": fps, "sourceFps": source_fps,
                           "skip": max(1, round(source_fps / fps)), "iterations": iterations,
                           "spirula": spirula, "spirulaVersion": SPIRULA_VERSION,
                           "decoder": decoder, "ffmpeg": ffmpeg,
                           "maskModel": str(model) if model else None,
                           "maskModelSha256": digest(model) if model else None}}
    job.mkdir(parents=True, exist_ok=False)
    write(job / "job.json", config)
    write(job / "probe.json", metadata)
    write(job / "state.json", {"status": "created", "stages": {}, "reviews": {}, "configSha256": digest(job / "job.json")})
    return config


@contextlib.contextmanager
def locked(job):
    # Never infer a dead worker from an old timestamp: GPU jobs can take days.
    path = job / ".lock"
    try:
        fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise CaptureError("ジョブはロック中です。稼働プロセスがないことを確認後、.lockを手動で除去してください。") from None
    try:
        with os.fdopen(fd, "w") as f:
            f.write(str(os.getpid()))
        yield
    finally:
        path.unlink()


def successful(state, stage):
    record = state["stages"].get(stage, {})
    if record.get("status") != "succeeded":
        raise CaptureError(f"先に {stage} を正常終了させてください。")
    return Path(record["output"])


def reviewed(state, stage):
    folder = successful(state, stage)
    review = state["reviews"].get(stage, {})
    snapshot = state["stages"][stage].get("artifacts")
    if not snapshot or not review.get("passed") or review.get("artifacts") != snapshot or snapshot != fingerprint(folder):
        raise CaptureError(f"{stage} の成果物の品質確認が必要です。reviewコマンドで結果と根拠を記録してください。")


def commands(config, state, stage, output):
    s = config["settings"]
    executable = s["spirula"]
    if stage == "extract":
        if s.get("decoder", "spirula") == "ffmpeg":
            argv = [s["ffmpeg"], "-nostdin", "-hide_banner", "-loglevel", "warning", "-n",
                    "-noautorotate", "-i", config["source"]["path"]]
            for i in range(2):
                argv += ["-map", f"0:V:{i}", "-vf", f"select=not(mod(n\\,{s['skip']}))",
                         "-fps_mode", "passthrough", "-q:v", "2", "-start_number", "0",
                         str(output / "images" / f"cam{i}" / "decoded_%08d.jpg")]
            return [argv]
        return [[executable, "sam", "extract", config["source"]["path"],
                 "--out", str(output / "images"), "--skip", str(s["skip"]),
                 "--keep", "1", "--360", "off", "--sync", "--no-autorotate"]]
    images = successful(state, "extract") / "images"
    if stage == "mask":
        if not s["maskModel"]:
            raise CaptureError("マスクモデル未指定。モデル付きの新規ジョブを作成してください。")
        return [[executable, "sam", "track", "--model", s["maskModel"],
                 "--frames", str(images / cam), "--text", "person; car; animal",
                 "--out", str(output / "masks" / cam)] for cam in ("cam0", "cam1")]
    if stage == "sfm":
        argv = [executable, "sfm", "auto", str(images), "-o", str(output),
                "--data-type", "video", "--quality", "high",
                "--camera-model", "opencv-fisheye", "--rig", "dual-fisheye=cam0,cam1",
                "--telemetry", config["source"]["path"]]
        if s["maskModel"]:
            argv += ["--masks", str(successful(state, "mask") / "masks")]
        else:
            argv += ["--no-masks"]
        return [argv]
    argv = [executable, "train", "360-camera", "--data", str(successful(state, "sfm")),
            "--image-dir", str(images), "--output-dir-prefix", str(output), "--output-dir-name", "training",
            "--num-iterations", str(s["iterations"]), "--steps-per-save", str(s["iterations"]),
            "--disable-viewer", "true", "--keep-viewer-alive", "false",
            "--auto-scale-poses", "false", "--scene-center", "none", "--train-frame", "points",
            "--warp-to-pinhole", "false", "--warp-spherical-to-pinhole", "false"]
    if s["maskModel"]:
        argv += ["--mask-dir", str(successful(state, "mask") / "masks")]
    else:
        argv += ["--load-masks", "false"]
    return [argv]


def check_ply(path):
    with path.open("rb") as f:
        lines = []
        for _ in range(1024):
            line = f.readline(4096)
            lines.append(line.decode("ascii"))
            if line == b"end_header\n":
                break
        else:
            raise CaptureError("PLYヘッダーが不正です。")
        if lines[0] != "ply\n" or "format binary_little_endian 1.0\n" not in lines:
            raise CaptureError("Spirulaのbinary little-endian PLYが必要です。")
        count = next((int(l.split()[2]) for l in lines if l.startswith("element vertex ")), 0)
        props = [l.split()[2] for l in lines if l.startswith("property float ")]
        required = {"x", "y", "z", "opacity", *(f"f_dc_{i}" for i in range(3)),
                    *(f"scale_{i}" for i in range(3)), *(f"rot_{i}" for i in range(4))}
        if count < 1 or not required.issubset(props):
            raise CaptureError("Gaussian属性が不足しています。点群PLYを完成モデルにはできません。")
        if path.stat().st_size - f.tell() != count * len(props) * 4:
            raise CaptureError("PLY本体が欠損、または未対応のプロパティがあります。")
        unpack = struct.Struct("<" + "f" * len(props))
        for _ in range(count):
            if not all(math.isfinite(v) for v in unpack.unpack(f.read(unpack.size))):
                raise CaptureError("PLYにNaN/Infが含まれています。")
    return {"gaussians": count, "sha256": digest(path)}


def normalize_outputs(stage, folder, config, state):
    if stage == "extract":
        if config["settings"].get("decoder") == "ffmpeg":
            for cam in ("cam0", "cam1"):
                paths = sorted((folder / "images" / cam).glob("decoded_*.jpg"))
                for i, p in enumerate(paths):
                    if p.stem != f"decoded_{i:08d}":
                        raise CaptureError("FFmpeg抽出画像の連番が欠落しています。")
                    p.rename(p.with_name(f"{i * config['settings']['skip']:05d}.jpg"))
    if stage == "mask":
        for cam in ("cam0", "cam1"):
            images = sorted((successful(state, "extract") / "images" / cam).glob("*.jpg"))
            masks = sorted((folder / "masks" / cam).glob("frame_*.png"))
            if len(masks) != len(images):
                raise CaptureError("画像とマスクの枚数が不一致です。")
            for i, image in enumerate(images):
                source = folder / "masks" / cam / f"frame_{i:05d}.png"
                source.rename(source.with_name(image.stem + ".png"))


def validate(stage, folder, config, state=None):
    normalize_outputs(stage, folder, config, state)
    return inspect_artifacts(stage, folder, config, state, write_metadata=True)


def inspect_artifacts(stage, folder, config, state=None, *, write_metadata=False):
    """Read-only by default; only run's finalization creates index sidecars."""
    if stage == "extract":
        a = sorted((folder / "images/cam0").glob("*.jpg"))
        b = sorted((folder / "images/cam1").glob("*.jpg"))
        if len(a) < 3 or [p.name for p in a] != [p.name for p in b] or any(p.stat().st_size == 0 for p in a + b):
            raise CaptureError("抽出画像が不足、空、または2トラック間で同期していません。")
        index = [{"frameIndex": int(p.stem), "approximateSeconds": int(p.stem) / config["settings"]["sourceFps"],
                  "images": [f"images/cam0/{p.name}", f"images/cam1/{p.name}"]} for p in a]
        metadata = {"timeBasis": "frameIndex/sourceFps (approximate, not PTS)", "frames": index}
        if write_metadata:
            write(folder / "source-index.json", metadata)
        elif read(folder / "source-index.json") != metadata:
            raise CaptureError("抽出インデックスが画像と一致しません。")
        return {"timestamps": len(a), "images": len(a) * 2}
    if stage == "mask":
        total = 0
        for cam in ("cam0", "cam1"):
            images = sorted((successful(state, "extract") / "images" / cam).glob("*.jpg"))
            masks = sorted((folder / "masks" / cam).glob("*.png"))
            if [p.stem for p in images] != [p.stem for p in masks] or any(p.stat().st_size == 0 for p in masks):
                raise CaptureError("画像とマスクの対応が不一致、または空です。")
            total += len(masks)
        return {"masks": total}
    if stage == "sfm":
        models = list((folder / "sparse").glob("*/images.bin"))
        if len(models) != 1:
            raise CaptureError("復元が空または複数モデルに分断されています。ログと撮影経路を確認してください。")
        poses, model_stats = check_sfm_model(models[0].parent)
        if len(poses) < 3:
            raise CaptureError("位置推定に成功した画像が3枚未満です。")
        extracted = successful(state, "extract")
        source_index = read(extracted / "source-index.json")
        names = {p["image"] for p in poses}
        for frame in source_index["frames"]:
            frame["registeredImages"] = [n for n in frame["images"] if n.removeprefix("images/") in names]
        known = {n.removeprefix("images/") for f in source_index["frames"] for n in f["images"]}
        if not names.issubset(known):
            raise CaptureError("SfM画像名が元の抽出画像と対応していません。")
        cameras = {"coordinates": "SfM output; world-to-camera; quaternion wxyz", "cameras": poses}
        if write_metadata:
            write(folder / "source-index.json", source_index)
            write(folder / "cameras.json", cameras)
        elif read(folder / "source-index.json") != source_index or read(folder / "cameras.json") != json.loads(json.dumps(cameras)):
            raise CaptureError("SfMのインデックス・カメラ情報が本体と一致しません。")
        return {**model_stats, "model": models[0].parent.relative_to(folder).as_posix(), "geometryReviewRequired": True,
                "registeredImages": len(poses), "extractedImages": len(known),
                "registeredTimestamps": sum(bool(f["registeredImages"]) for f in source_index["frames"]),
                "extractedTimestamps": len(source_index["frames"])}
    ply = folder / "training" / f"step-{config['settings']['iterations']:09d}.ckpt" / "splat.ply"
    if not ply.is_file():
        raise CaptureError("最終反復のsplat.plyがありません。途中チェックポイントは採用しません。")
    return check_ply(ply)


# Parameter counts in the pinned Spirula COLMAP binary format (models 0..17).
CAMERA_PARAMETERS = (3, 4, 4, 5, 8, 8, 12, 5, 4, 5, 12, 14, 4, 5, 4, 5, 6, 2)


class BinaryReader:
    def __init__(self, stream):
        self.stream = stream
        self.size = os.fstat(stream.fileno()).st_size
        self.name = Path(stream.name).name

    def fail(self, reason):
        raise CaptureError(f"{self.name}: {reason}")

    def unpack(self, fmt):
        size = struct.calcsize(fmt)
        data = self.stream.read(size)
        if len(data) != size:
            self.fail("本体またはヘッダーが欠損しています。")
        return struct.unpack(fmt, data)

    def count(self, minimum_bytes, nonempty=False):
        count, = self.unpack("<Q")
        if (nonempty and not count) or count > (self.size - self.stream.tell()) // minimum_bytes:
            self.fail("件数が空、または本体サイズと一致しません。")
        return count

    def end(self):
        if self.stream.read(1):
            self.fail("予期しない余剰データがあります。")


def read_images(path):
    poses, observations = [], {}
    with Path(path).open("rb") as f:
        r = BinaryReader(f)
        for _ in range(r.count(73, nonempty=True)):
            row = r.unpack("<i7di")
            if row[0] < 0 or row[0] in observations or row[8] < 0:
                r.fail("画像IDが重複、またはIDが不正です。")
            if not all(math.isfinite(v) for v in row[1:8]) or not any(row[1:5]):
                r.fail("カメラ姿勢が不正です。")
            name = bytearray()
            while True:
                b = f.read(1)
                if b == b"\0":
                    break
                if not b or len(name) >= 8192:
                    r.fail("画像名が不正です。")
                name += b
            try:
                image = name.decode("utf-8")
            except UnicodeDecodeError:
                r.fail("画像名がUTF-8ではありません。")
            if not image:
                r.fail("画像名が空です。")
            refs = []
            for _ in range(r.count(24)):
                x, y, point = r.unpack("<ddq")
                if not all(math.isfinite(v) for v in (x, y)) or point < -1:
                    r.fail("2D観測値が不正です。")
                refs.append(point)
            observations[row[0]] = refs
            poses.append({"id": row[0], "qvec": row[1:5], "tvec": row[5:8],
                          "cameraId": row[8], "image": image})
        r.end()
    if len({p["image"] for p in poses}) != len(poses):
        raise CaptureError("SfM画像名が重複しています。")
    return poses, observations


def read_poses(path):
    return read_images(path)[0]


def check_sfm_model(folder):
    cameras = set()
    with (folder / "cameras.bin").open("rb") as f:
        r = BinaryReader(f)
        for _ in range(r.count(40, nonempty=True)):
            cid, model, width, height = r.unpack("<iiQQ")
            if cid < 0 or cid in cameras or not width or not height:
                r.fail("カメラIDまたは画像サイズが不正です。")
            if not 0 <= model < len(CAMERA_PARAMETERS):
                r.fail(f"未対応のカメラモデルID: {model}")
            params = r.unpack("<" + "d" * CAMERA_PARAMETERS[model])
            if not all(math.isfinite(v) for v in params):
                r.fail("内部パラメータにNaN/Infがあります。")
            cameras.add(cid)
        r.end()
    poses, observations = read_images(folder / "images.bin")
    if any(p["cameraId"] not in cameras for p in poses):
        raise CaptureError("images.binが存在しないカメラを参照しています。")
    points, tracks = set(), set()
    with (folder / "points3D.bin").open("rb") as f:
        r = BinaryReader(f)
        for _ in range(r.count(51, nonempty=True)):
            row = r.unpack("<Q3d3Bd")
            pid = row[0]
            if pid in points or pid >= 2**63 or not all(math.isfinite(v) for v in (*row[1:4], row[7])) or row[7] < 0:
                r.fail("点ID、座標または再投影誤差が不正です。")
            points.add(pid)
            for _ in range(r.count(8, nonempty=True)):
                iid, index = r.unpack("<ii")
                ref = (iid, index)
                if iid not in observations or not 0 <= index < len(observations[iid]):
                    r.fail("トラックの画像・2D観測参照が不正です。")
                if ref in tracks or observations[iid][index] != pid:
                    r.fail("トラックが重複、または画像側の3D点参照と一致しません。")
                tracks.add(ref)
        r.end()
    for iid, refs in observations.items():
        for index, pid in enumerate(refs):
            if pid != -1 and (pid not in points or (iid, index) not in tracks):
                raise CaptureError("images.binの3D点参照に対応するトラックがありません。")
    return poses, {"cameras": len(cameras), "points3D": len(points), "observations": len(tracks)}


def execute(argv, log):
    with log.open("wb") as stream:
        process = subprocess.Popen(argv, stdout=stream, stderr=subprocess.STDOUT,
                                   start_new_session=(os.name != "nt"))
        try:
            code = process.wait()
        except BaseException:
            if os.name == "nt":
                process.terminate()
            else:
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                if os.name == "nt":
                    process.kill()
                else:
                    os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
    if code:
        raise CaptureError(f"外部処理の終了コード: {code}。ログ: {log}")


def run(job, stage):
    job = Path(job).resolve()
    with locked(job):
        state = read(job / "state.json")
        if stage not in STAGES:
            raise CaptureError(f"不明な段階: {stage}")
        if state["stages"].get(stage, {}).get("status") == "succeeded":
            raise CaptureError("成功済みの段階は再実行しません。条件を変える場合は新規ジョブを作成してください。")
        attempt = job / "attempts" / (stage + "-" + uuid.uuid4().hex[:12])
        output = attempt / "output"
        attempt.mkdir(parents=True)
        record = {"status": "running", "phase": "preflight", "output": str(output),
                  "attempt": str(attempt), "startedAt": time.time()}
        state["stages"][stage] = record
        state["status"] = "running"
        write(job / "state.json", state)
        try:
            config = read(job / "job.json")
            if digest(job / "job.json") != state["configSha256"]:
                raise CaptureError("設定が作成時から変更されています。新規ジョブを作成してください。")
            if digest(config["source"]["path"]) != config["source"]["sha256"]:
                raise CaptureError("入力ファイルが作成時から変更されています。")
            s = config["settings"]
            if s["maskModel"] and digest(s["maskModel"]) != s["maskModelSha256"]:
                raise CaptureError("マスクモデルが作成時から変更されています。")
            if stage in ("mask", "sfm", "train"):
                reviewed(state, "extract")
            if stage in ("sfm", "train") and s["maskModel"]:
                reviewed(state, "mask")
            if stage == "train":
                reviewed(state, "sfm")
            is_ffmpeg = stage == "extract" and s.get("decoder") == "ffmpeg"
            executable = tool(s["ffmpeg"] if is_ffmpeg else s["spirula"])
            version = capture([executable, *(["-version"] if is_ffmpeg else ["sfm", "--version"])]).strip()
            if not is_ffmpeg and SPIRULA_VERSION not in version:
                raise CaptureError(f"対応版はSpirula {SPIRULA_VERSION}です。検出: {version}")
            binary = {"path": executable, "sha256": digest(executable), "version": version}
            engine_key = "decoder" if is_ffmpeg else "engine"
            if state.get(engine_key) and state[engine_key] != binary:
                raise CaptureError("実行ファイルが変更されています。新規ジョブを作成してください。")
            state[engine_key] = binary
            output.mkdir()
            if is_ffmpeg:
                for cam in ("cam0", "cam1"):
                    (output / "images" / cam).mkdir(parents=True)
            argv_list = commands(config, state, stage, output)
            for argv in argv_list:
                argv[0] = executable
            write(attempt / "commands.json", argv_list)
            record["phase"] = "execute"
            write(job / "state.json", state)
            for i, argv in enumerate(argv_list):
                execute(argv, attempt / f"{i:02d}.log")
            record["phase"] = "validate"
            record["validation"] = validate(stage, output, config, state)
            record["artifacts"] = fingerprint(output)
            record["status"] = "succeeded"
            record["phase"] = "complete"
            state["status"] = "awaiting_review"
        except BaseException as exc:
            record["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
            record["error"] = str(exc) or type(exc).__name__
            state["status"] = record["status"]
            raise
        finally:
            record["finishedAt"] = time.time()
            write(job / "state.json", state)
            write(attempt / "result.json", record)
    return state


def review(job, stage, passed, note):
    job = Path(job).resolve()
    if not note.strip():
        raise CaptureError("確認した内容・根拠をnoteに記録してください。")
    with locked(job):
        state = read(job / "state.json")
        folder = successful(state, stage)
        if any(state["stages"].get(s, {}).get("status") == "succeeded" for s in STAGES[STAGES.index(stage) + 1:]):
            raise CaptureError("後続段階が存在します。上流の品質判定を変える場合は新規ジョブを作成してください。")
        entry = {"passed": False, "note": note, "at": time.time()}
        if passed:
            try:
                config = read(job / "job.json")
                if digest(job / "job.json") != state["configSha256"]:
                    raise CaptureError("設定が変更されています。")
                snapshot = state["stages"][stage].get("artifacts")
                if not snapshot:
                    raise CaptureError("成功時の成果物ハッシュがない旧試行です。新規ジョブで再実行してください。")
                if fingerprint(folder) != snapshot:
                    raise CaptureError("成果物が成功時から変更されています。手編集は新規ジョブで処理してください。")
                inspect_artifacts(stage, folder, config, state)
                if fingerprint(folder) != snapshot:
                    raise CaptureError("検査中に成果物が変更されました。")
                entry.update(passed=True, artifacts=snapshot)
            except (CaptureError, OSError, ValueError, KeyError, struct.error) as exc:
                entry['error'] = str(exc)
                state['reviews'][stage] = entry
                state['status'] = 'rejected'
                write(job / 'state.json', state)
                raise
        state["reviews"][stage] = entry
        state["status"] = "reviewed" if passed else "rejected"
        write(job / "state.json", state)
    return state
