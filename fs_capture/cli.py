import argparse
import json
from pathlib import Path
import subprocess
import struct
import sys

from .core import (STAGES, CaptureError, commands,
                   create, read, review, run)
from .diagnostics import diagnose


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
        if args.action == "init":
            result = create(args.source, args.job, args.fps, args.iterations, args.spirula, args.ffprobe,
                            args.mask_model, args.decoder, args.ffmpeg)
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
