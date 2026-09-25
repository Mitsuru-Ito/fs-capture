# FS Capture

DJI Osmo 360のオリジナルOSVから静的な3D Gaussian Splattingを生成し、将来FieldSplatへ渡すためのローカル処理基盤です。

**現状は段階①「処理経路の実証」に向けたCLIです。実OSVの5位置・10画像からGPU生成とブラウザ表示まで確認しましたが、画質は歩行用途の受入水準に達していません。SOG変換、衝突形状、ブラウザ歩行、FieldSplat接続は未検証・未実装です。歩行アプリの完成版ではありません。** ダミー生成データを復元成功として扱う機能はありません。

## 要件

- Python 3.10以上。Python側の外部パッケージは不要。
- FFmpegの`ffprobe`。
- [Spirula Studio v2026.9.20](https://github.com/harry7557558/spirula-studio/releases/tag/v2026.9.20)。実行ファイルは別途配置。
- Spirulaが動作するGPU・ドライバ。ネイティブ動画抽出にはVulkan Video対応が必要。M3 Max実機ではGPU認識と別途行った5位置のSfM診断は成功しましたが、ネイティブ動画抽出は非対応でした。Macでは`--decoder ffmpeg`を使用します。5位置・10画像での10,000反復の実GPU学習は完了しました。
- オリジナルの短いOSV（まず1〜3分）。初期版は同一解像度・同一フレームレート・同一開始時刻の2映像トラックに限定。
- マスクを使う場合は、対応するSAMモデルを事前配置。

GPU要件を満たせない場合は処理を失敗として記録します。パッケージ、モデル、実行ファイルを自動ダウンロードしません。元動画は移動・上書きせず参照し、SHA-256を保存します。別途バックアップしてください。

## 段階別の診断

```sh
python3 -m fs_capture doctor --spirula /path/to/spirula --decoder ffmpeg
python3 -m fs_capture doctor --spirula /path/to/spirula --decoder ffmpeg \
  --source /path/to/clip.OSV --check-gpu
python3 -m fs_capture doctor --spirula /path/to/spirula --job work/room-01
```

診断結果はJSONです。ツールの存在・版は`tools`、実処理の証拠は`checks`に分けます。

| 項目 | 確認範囲 |
| --- | --- |
| tools | Spirula・ffprobe・ffmpegの存在と版。Spirulaは対象版との一致も確認 |
| input | `--source`の両魚眼トラックのメタデータ |
| decode | `--decoder ffmpeg --source …`で両魚眼の先頭1フレームを一時領域へ実デコードし、終了後に削除。全区間・同期・画質の保証ではない |
| gpu | `--check-gpu`でSpirulaが使用可と報告するVulkan GPUを列挙。CPUのみ・使用不可はFAILED、解釈できない出力はUNKNOWN |
| training | `--job`で既存の学習成功記録、設定・入力・実行ファイルの識別情報、最終PLYを照合。今回の学習実行・画質承認ではない |

未実施は`NOT_TESTED`です。Spirulaネイティブデコードはdoctorでは実行せず、実ジョブのextractで確認します。学習を自動で始めることもありません。終了コード0は必須ツール確認と指定した検査に失敗がなかったことを表し、未検証の段階まで使用可能という意味ではありません。GPU認識成功と学習実行成功は独立です。ログの絶対パス等は公開前に確認してください。

## 開始

リポジトリ直下で実行します。

```sh
python3 -m fs_capture doctor --spirula /path/to/spirula
python3 -m fs_capture init /path/to/clip.OSV work/room-01 \
  --spirula /path/to/spirula --fps 3 --mask-model /path/to/sam-model.ggml
python3 -m fs_capture plan work/room-01 extract
python3 -m fs_capture run work/room-01 extract
python3 -m fs_capture status work/room-01
```

Macなど、Vulkan Videoを利用できない環境では、初期化時に`--decoder ffmpeg`を追加してください。FFmpegで両魚眼トラックの同じフレーム番号を抽出します。スティッチ・ピンホール変換・回転は行いません。位置推定と学習は引き続きSpirulaを使います。`--ffmpeg /path/to/ffmpeg`も指定できます。

```sh
python3 -m fs_capture init /path/to/clip.OSV work/room-mac \
  --spirula /path/to/spirula --decoder ffmpeg --fps 3
python3 -m fs_capture run work/room-mac extract
```

2026-09-24のM3 Max実素材試験では、3840×3840・25fps・10.28秒の2トラックOSVから33時刻・66枚の画像を抽出できました。これはデコード・抽出の成功であり、3D復元の成功ではありません。人物を含む素材やほぼ固定撮影の素材は、マスクと撮影位置の検証が別途必要です。

同日、同一空間に見える5本から各1時刻・両魚眼を取り出した別診断では、10枚すべてが1モデルへ登録されました。単一動画CLIとは別の試行です。詳しくは[実素材検証の結果](docs/real-capture-check.md)を参照してください。

`--fps`は目標値です。元動画fpsから整数間隔を計算するため、例えば29.97fpsを10フレームごとに抽出すると2.997fpsになります。現在は一定間隔・同期抽出です。ブレと重複を選別する適応抽出は後続課題です。

各段階の成果物を実際に確認した後、確認内容と根拠を記録します。次の段階はこの記録がある場合に進みます。

```sh
python3 -m fs_capture review work/room-01 extract --result pass \
  --note '両レンズの同期、必要な区間、ブレを画像で確認。確認資料: …'
python3 -m fs_capture run work/room-01 mask
python3 -m fs_capture review work/room-01 mask --result pass \
  --note '撮影者・移動物体の除去と静的な壁の保持を確認。確認資料: …'
python3 -m fs_capture run work/room-01 sfm
python3 -m fs_capture review work/room-01 sfm --result pass \
  --note 'カメラ経路、区間の欠落、二重化を確認。確認資料: …'
python3 -m fs_capture run work/room-01 train
```

上記のnoteは記入例です。品質確認を行わずにそのまま実行しないでください。`fail`を記録すると次の段階へ進めません。`--mask-model`を省略した場合はmask段階を飛ばせますが、extractの確認で撮影者・動く物体・魚眼周辺部の扱いを明示してください。境界マスクの自動生成は未実装です。

終了コード0だけでは成功としません。抽出の同期、マスクの対応、SfMの単一モデル・バイナリ本体・観測参照の整合性、最終反復のPLYのGaussian属性・本体サイズ・有限値を検査します。これらは形式検査であり、復元品質は保証しません。

## ジョブと再実行

```text
work/room-01/
  job.json                  入力パス、ハッシュ、設定
  probe.json                元動画のトラック情報
  state.json                状態、確認結果、エンジン識別情報
  attempts/<stage>-<id>/
    commands.json           実際に渡した引数列（実行前検査を通過した場合）
    result.json             試行ごとの結果・失敗理由・時刻・失敗した処理区分
    00.log                  外部ツールの標準出力・標準エラー
    output/                 その試行の成果物
```

- 元OSVの消失、設定の不整合、ツール不足、品質ゲートなど実行前検査の失敗も、`state.json`と試行の`result.json`に保存します。`phase`はpreflight / execute / validate / completeです。状態ファイル自体が読めない場合やロック競合、成功済み段階の再実行拒否では既存の状態を上書きしません。
- `run JOB STAGE`は一段階ずつ実行。失敗・キャンセル後は同じコマンドで新しい試行を作成し、成功済みの上流段階を再利用します。失敗した試行も保持します。
- 同じ段階の途中から再開する機能はありません。成功済み段階や設定を変える場合は別のジョブを作成します。
- Ctrl+Cで子プロセスを停止し、状態を保存。二重実行はロックで防止します。強制終了・停電後にロックが残った場合は、`.lock`のPIDと子プロセスが停止していることを確認してからロックだけを除去します。
- 入力・モデル・設定・Spirula実行ファイルの変更は拒否します。確認済み成果物もハッシュで確認し、差し替え後の無条件な続行を拒否します。大きな成果物ではこの検査に時間がかかります。
- 元映像・モデル・成果物はGitへ登録しません。ログにはローカルパス等を含むため公開前に確認してください。

## 座標と時刻

`--360 off --sync`で魚眼の同期を維持し、SfMに`opencv-fisheye`と`dual-fisheye=cam0,cam1`を指定します。レンズ校正の妥当性は実OSVで確認が必要です。

学習は`--train-frame points --scene-center none`でSfMの点の座標を維持します。Spirula内のビューア用正規化と、外部へ書き出すPLYの座標は別物です。`normalized`は対象版の学習設定では受け付けられないため使いません。

SfM段階には`cameras.json`（world-to-camera、quaternion wxyz）と`source-index.json`（元フレームと登録成否）を保存します。時刻は`frameIndex/sourceFps`の近似値で、厳密なPTSではありません。画像枚数と時刻数を別々に記録します。IMU由来の縮尺・上下方向も無条件に正しいとは扱いません。

## 次の実素材検証

固定5位置での生成・閲覧試験は完了していますが、画質は不合格です。次は以下を検証します。

1. マスク・投影条件を比較し、既存素材で改善できる範囲を切り分ける。
2. 移動撮影を比較対象に加え、撮影点と横にずれた視点で画質・観測不足を確認する。
3. 既知距離による縮尺・向き・床の調整を一つの変換行列で定義。表示、衝突形状、カメラ、注釈に同じ変換を適用。
4. `splat-transform`の採用版を固定してSOGと衝突形状を生成。床の穴、壁抜け、出入口を補修。
5. FieldSplatの実装を確認して接続するか、閲覧部分を実装。PC 1080p/30fps、歩行、別PC/LAN、オフラインの受入試験へ進む。

初期版には公開コマンドがありません。PLY生成成功や人手のreviewだけで「歩行確認済み」「公開可能」とは判定しません。詳しい判定項目は[受入条件](docs/acceptance.md)を参照してください。

## テスト

```sh
python3 -m unittest discover -s tests -v
```

試験用の小さな合成ファイルと外部処理のテストダブルで、失敗処理・品質ゲート・入力改変・データ形式を検証します。合成2トラック動画を用いるFFmpeg抽出・診断の統合試験も含みます。GPU生成、実OSVの画質、歩行の受入試験とは別です。Phase Aの結果は[信頼性修正の検証記録](docs/phase-a-verification.md)を参照してください。

## 根拠と依存関係

実装時に確認したSpirulaの参照コミットは`cd93c75114f591419e394328ba76f33b721da73a`（v2026.9.20）です。

- [抽出CLI](https://github.com/harry7557558/spirula-studio/blob/v2026.9.20/src/app/cli/sam_extract.cpp)
- [マスクCLI](https://github.com/harry7557558/spirula-studio/blob/v2026.9.20/src/app/cli/sam_main.cpp)
- [SfM CLI](https://github.com/harry7557558/spirula-studio/blob/v2026.9.20/src/app/cli/sfm_main.cpp)
- [学習設定](https://github.com/harry7557558/spirula-studio/blob/v2026.9.20/src/config/TrainConfig.h)
- [学習の制約と座標処理](https://github.com/harry7557558/spirula-studio/blob/v2026.9.20/src/app/TrainerCore.cpp)
- [SOG配信](https://developer.playcanvas.com/user-manual/splat-transform/streamed-sog/)、[衝突形状](https://developer.playcanvas.com/user-manual/splat-transform/collision/)

Spirula本体・学習済みモデルは同梱しません。配布時は各ツール・モデルのライセンスを別途確認します。外部プロセスとして呼ぶことだけを配布条件の根拠にはしません。本リポジトリの公開ライセンスはまだ選定していません。
