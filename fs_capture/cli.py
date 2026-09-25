import argparse
import json
from pathlib import Path
import subprocess
import struct
import sys

from .core import (STAGES, CaptureError, commands,
                   create, read, review, run)
from .diagnostics import diagnose
from .collection import create_manifest
from .report import report, capture_check


def main(argv=None):
    parser = argparse.ArgumentParser(description="FS Capture: Osmo 360実素材の段階的な3DGS検証")
    sub = parser.add_subparsers(dest="action", required=True)
    doctor = sub.add_parser("doctor", help="導入・入力・デコード・GPU・既存学習記録を分けて診断")
    doctor.add_argument("--spirula", default="spirula")
    doctor.add_argument("--ffprobe", default="ffprobe")
    doctor.add_argument("--ffmpeg", default="ffmpeg")
    doctor.add_argument("--decoder", choices=("spirula", "ffmpeg"), default="spirula")
    doctor.add_argument("--source", help="メタデータとFFmpeg経路の先頭フレームを検査するOSV")
    doctor.add_argument("--check-gpu", action="store_true", help="SpirulaのGPU列挙を実行")
    doctor.add_argument("--job", help="既存ジョブの学習成功記録と最終PLYを照合（再学習しない）")
    init = sub.add_parser("init", help="OSVを検査し、入力ハッシュと設定を保存")
    init.add_argument("source")
    init.add_argument("job")
    init.add_argument("--fps", type=float, default=3)
    init.add_argument("--iterations", type=int, default=30000)
    init.add_argument("--spirula", default="spirula")
    init.add_argument("--ffprobe", default="ffprobe")
    init.add_argument("--mask-model", help="事前に取得したSAMモデルのパス")
    init.add_argument("--decoder", choices=("spirula", "ffmpeg"), default="spirula",
                      help="Mac等でVulkan動画デコードが使えない場合はffmpeg")
    init.add_argument("--ffmpeg", default="ffmpeg")
    manifest = sub.add_parser("init-manifest", help="複数OSVのmanifestを新規ジョブに登録")
    manifest.add_argument("manifest")
    manifest.add_argument("job")
    manifest.add_argument("--spirula", default="spirula")
    manifest.add_argument("--ffprobe", default="ffprobe")
    manifest.add_argument("--ffmpeg", default="ffmpeg")
    rep = sub.add_parser("report", help="内部用の画像・マスク・登録状態レポートを生成")
    rep.add_argument("job")
    qa = sub.add_parser("capture-check", help="必須確認箇所の撮影状況を記録")
    qa.add_argument("job")
    qa.add_argument("label")
    qa.add_argument("--status", required=True, choices=("CAPTURED", "NEEDS_CAPTURE", "NOT_TESTED"))
    qa.add_argument("--note", required=True)
    qa.add_argument("--reviewer", required=True)
    qa.add_argument("--reference", action="append", default=[], help="確認対象 stage:relative/path を指定（繰り返し可）")
    preview = sub.add_parser("preview", help="最終PLYの内部3D確認画面を作成・ローカル配信")
    preview.add_argument("job")
    preview.add_argument("--viewer-assets", required=True, help="固定版Spirulaソースのルート（取得は行わない）")
    preview.add_argument("--fov", type=float, default=90)
    preview.add_argument("--size", type=int, default=768)
    preview.add_argument("--port", type=int, default=8768)
    preview.add_argument("--prepare-only", action="store_true")
    ps = sub.add_parser("preview-serve", help="既存プレビューと取り込んだ視点をローカル配信")
    ps.add_argument("preview")
    ps.add_argument("--port", type=int, default=8768)
    pi = sub.add_parser("preview-import", help="ブラウザから保存した視点・判定・証拠を検査して取り込む")
    pi.add_argument("preview")
    pi.add_argument("record")
    for action in ("plan", "run"):
        p = sub.add_parser(action, help="実行コマンドを表示" if action == "plan" else "1段階を実行")
        p.add_argument("job")
        p.add_argument("stage", choices=STAGES)
    status = sub.add_parser("status", help="状態・失敗原因・成果物を表示")
    status.add_argument("job")
    check = sub.add_parser("review", help="成果物の実際の品質確認結果を記録")
    check.add_argument("job")
    check.add_argument("stage", choices=STAGES)
    check.add_argument("--result", required=True, choices=("pass", "fail"))
    check.add_argument("--note", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "doctor":
            result, code = diagnose(args.spirula, args.ffprobe, args.ffmpeg, args.decoder,
                                    args.source, args.check_gpu, args.job)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return code
        if args.action in ("preview", "preview-serve", "preview-import"):
            from .preview import prepare, serve, import_review
            if args.action == "preview-import":
                result = import_review(args.preview, args.record)
            else:
                folder = prepare(args.job, args.viewer_assets, args.fov, args.size) if args.action == "preview" else Path(args.preview)
                if args.action == "preview" and args.prepare_only:
                    result = {"preview": str(folder)}
                else:
                    serve(folder, args.port)
                    return 0
        elif args.action == "init":
            result = create(args.source, args.job, args.fps, args.iterations, args.spirula, args.ffprobe,
                            args.mask_model, args.decoder, args.ffmpeg)
        elif args.action == "init-manifest":
            result = create_manifest(args.manifest, args.job, args.spirula, args.ffprobe, args.ffmpeg)
        elif args.action == "report":
            result = report(args.job)
        elif args.action == "capture-check":
            result = capture_check(args.job, args.label, args.status, args.note, args.reviewer, args.reference)
        elif args.action == "status":
            result = read(Path(args.job) / "state.json")
        elif args.action == "plan":
            job = Path(args.job).resolve()
            result = commands(read(job / "job.json"), read(job / "state.json"), args.stage, job / "attempts/PLAN/output")
        elif args.action == "run":
            result = run(args.job, args.stage)
        else:
            result = review(args.job, args.stage, args.result == "pass", args.note)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("中断しました。ジョブ状態とログを保存しました。", file=sys.stderr)
        return 130
    except (CaptureError, OSError, ValueError, KeyError, ZeroDivisionError, subprocess.TimeoutExpired, struct.error) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
