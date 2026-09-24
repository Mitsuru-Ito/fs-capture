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
    return {p.relative_to(root).as_posix(): digest(p)
            for p in sorted(root.rglob("*")) if p.is_file()}


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


def create(source, job, fps=3, iterations=30000, spirula="spirula", ffprobe="ffprobe", mask_model=None):
    source, job = Path(source).resolve(), Path(job).resolve()
    if source.suffix.lower() != ".osv" or not source.is_file() or source.stat().st_size == 0:
        raise CaptureError("空でないオリジナルOSVファイルを指定してください。")
    if not math.isfinite(fps) or fps <= 0 or iterations < 1:
        raise CaptureError("fpsとiterationsは正の値が必要です。")
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
    if not review.get("passed") or review.get("artifacts") != fingerprint(folder):
        raise CaptureError(f"{stage} の成果物の品質確認が必要です。reviewコマンドで結果と根拠を記録してください。")


def commands(config, state, stage, output):
    s = config["settings"]
    executable = s["spirula"]
    if stage == "extract":
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


def validate(stage, folder, config, state=None):
    if stage == "extract":
        a = sorted((folder / "images/cam0").glob("*.jpg"))
        b = sorted((folder / "images/cam1").glob("*.jpg"))
        if len(a) < 3 or [p.name for p in a] != [p.name for p in b] or any(p.stat().st_size == 0 for p in a + b):
            raise CaptureError("抽出画像が不足、空、または2トラック間で同期していません。")
        index = [{"frameIndex": int(p.stem), "approximateSeconds": int(p.stem) / config["settings"]["sourceFps"],
                  "images": [f"images/cam0/{p.name}", f"images/cam1/{p.name}"]} for p in a]
        write(folder / "source-index.json", {"timeBasis": "frameIndex/sourceFps (approximate, not PTS)", "frames": index})
        return {"timestamps": len(a), "images": len(a) * 2}
    if stage == "mask":
        total = 0
        for cam in ("cam0", "cam1"):
            images = sorted((successful(state, "extract") / "images" / cam).glob("*.jpg"))
            masks = sorted((folder / "masks" / cam).glob("frame_*.png"))
            if len(masks) != len(images) or any(p.stat().st_size == 0 for p in masks):
                raise CaptureError("画像とマスクの枚数が不一致、または空のマスクがあります。")
            # SAM track numbers output sequentially; SfM matches the input stems.
            for i, p in enumerate(images):
                (folder / "masks" / cam / f"frame_{i:05d}.png").rename(folder / "masks" / cam / (p.stem + ".png"))
            total += len(masks)
        return {"masks": total}
    if stage == "sfm":
        models = list((folder / "sparse").glob("*/images.bin"))
        if len(models) != 1:
            raise CaptureError("復元が空または複数モデルに分断されています。ログと撮影経路を確認してください。")
        for name in ("cameras.bin", "images.bin", "points3D.bin"):
            p = models[0].parent / name
            with p.open("rb") as f:
                data = f.read(8)
            if len(data) < 8 or struct.unpack("<Q", data)[0] < 1:
                raise CaptureError(f"SfMの {name} が空です。")
        poses = read_poses(models[0])
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
        write(folder / "source-index.json", source_index)
        write(folder / "cameras.json", {"coordinates": "SfM output; world-to-camera; quaternion wxyz", "cameras": poses})
        return {"model": models[0].parent.relative_to(folder).as_posix(), "geometryReviewRequired": True,
                "registeredImages": len(poses), "extractedImages": len(known),
                "registeredTimestamps": sum(bool(f["registeredImages"]) for f in source_index["frames"]),
                "extractedTimestamps": len(source_index["frames"])}
    ply = folder / "training" / f"step-{config['settings']['iterations']:09d}.ckpt" / "splat.ply"
    if not ply.is_file():
        raise CaptureError("最終反復のsplat.plyがありません。途中チェックポイントは採用しません。")
    return check_ply(ply)


def read_poses(path):
    poses = []
    with path.open("rb") as f:
        count = struct.unpack("<Q", f.read(8))[0]
        if count > path.stat().st_size // 73:
            raise CaptureError("images.binの画像数が不正です。")
        for _ in range(count):
            row = struct.unpack("<i7di", f.read(64))
            if not all(math.isfinite(v) for v in row[1:8]):
                raise CaptureError("カメラ姿勢にNaN/Infがあります。")
            name = bytearray()
            while True:
                b = f.read(1)
                if b == b"\0":
                    break
                if not b or len(name) > 8192:
                    raise CaptureError("images.binの画像名が不正です。")
                name += b
            points = struct.unpack("<Q", f.read(8))[0]
            if points * 24 > path.stat().st_size - f.tell():
                raise CaptureError("images.binの観測データが欠損しています。")
            f.seek(points * 24, 1)
            poses.append({"id": row[0], "qvec": row[1:5], "tvec": row[5:8],
                          "cameraId": row[8], "image": name.decode("utf-8")})
        if f.read(1):
            raise CaptureError("images.binに予期しない余剰データがあります。")
    if len({p["image"] for p in poses}) != len(poses):
        raise CaptureError("SfM画像名が重複しています。")
    return poses


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
        config, state = read(job / "job.json"), read(job / "state.json")
        if digest(job / "job.json") != state["configSha256"]:
            raise CaptureError("設定が作成時から変更されています。新規ジョブを作成してください。")
        if state["stages"].get(stage, {}).get("status") == "succeeded":
            raise CaptureError("成功済みの段階は再実行しません。条件を変える場合は新規ジョブを作成してください。")
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
        executable = tool(s["spirula"])
        version = capture([executable, "sfm", "--version"]).strip()
        if SPIRULA_VERSION not in version:
            raise CaptureError(f"対応版はSpirula {SPIRULA_VERSION}です。検出: {version}")
        binary = {"path": executable, "sha256": digest(executable), "version": version}
        if state.get("engine") and state["engine"] != binary:
            raise CaptureError("Spirula実行ファイルが変更されています。新規ジョブを作成してください。")
        state["engine"] = binary
        attempt = job / "attempts" / (stage + "-" + uuid.uuid4().hex[:12])
        output = attempt / "output"
        output.mkdir(parents=True)
        argv_list = commands(config, state, stage, output)
        for argv in argv_list:
            argv[0] = executable
        write(attempt / "commands.json", argv_list)
        record = {"status": "running", "output": str(output), "attempt": str(attempt), "startedAt": time.time()}
        state["stages"][stage] = record
        state["status"] = "running"
        write(job / "state.json", state)
        try:
            for i, argv in enumerate(argv_list):
                execute(argv, attempt / f"{i:02d}.log")
            record["validation"] = validate(stage, output, config, state)
            record["status"] = "succeeded"
            state["status"] = "awaiting_review"
        except BaseException as exc:
            record["status"] = "cancelled" if isinstance(exc, KeyboardInterrupt) else "failed"
            record["error"] = str(exc) or type(exc).__name__
            state["status"] = record["status"]
            raise
        finally:
            record["finishedAt"] = time.time()
            write(job / "state.json", state)
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
        state["reviews"][stage] = {"passed": passed, "note": note, "at": time.time(), "artifacts": fingerprint(folder)}
        state["status"] = "reviewed" if passed else "rejected"
        write(job / "state.json", state)
    return state
