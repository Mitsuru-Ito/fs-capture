# 複数OSVの標準手順

`init SOURCE JOB`は従来の単一OSV用（job schema 1）として維持し、`init-manifest MANIFEST JOB`を追加した（job schema 2）。元ジョブを変換・上書きしない。manifest自体のschemaVersionは1。入力パスはmanifestのあるディレクトリを基準に解決する。実素材はGit管理外の場所に置く。

## 入力契約

[固定5位置の記入例](../examples/fixed-captures.json)をコピーして編集する。例のパスは架空であり、実素材は付属しない。

- `captureId`は1〜64文字の英数字・`_`・`-`。大文字小文字を無視して一意。ファイル名が同じでも別IDと別内容なら登録できる。同じファイル内容の重複は独立視点と扱わず拒否する。
- `mode: fixed`は`frames: [0]`のように1時刻だけ選ぶ。固定動画のフレーム数を撮影位置数に読み替えない。
- `mode: moving`は3時刻以上を昇順で選ぶ。例: `frames: [0, 8, 16, 24]`。または`range: {"startFrame": 0, "endFrameExclusive": 80, "stride": 8}`。framesとrangeは同時指定不可。上限は各素材10,000時刻。
- 全素材で解像度とレートが一致するCFRの両魚眼OSVに限定。トラック間の解像度・レート・開始時刻も検査する。メタデータ検査でCFRを完全保証するものではない。
- `imageWidth`は元幅以下。縦横比を維持して縮小し、両レンズの同じ元フレームを抽出する。回転・スティッチ・ピンホール変換はしない。
- `captureId / cam0またはcam1 / frameIndex`を一意なキーとする。近似時刻は各素材の`frameIndex/sourceFps`でありPTSではない。異なる素材の同じ番号を同時刻と扱わない。
- 同時刻性を決められない素材間のテレメトリを混ぜない。Spirulaには素材ごとの`dual-fisheye=<capture>/cam0,<capture>/cam1`リグを渡す。SfMは`individual`・`exhaustive`であり、大量フレームの効率化は今後の課題。
- `training`はiterations、resolutionDivisor、capMaxのみ指定。座標は従来どおりtrain-frame=points、scene-center=none、auto-scale-poses=falseを維持する。メートル校正はしない。

## 手修正マスク

maskingを指定する場合、全採用画像に対応する以下のファイルを用意する。masking自体を省略すればマスクなしの試験も可能だが、人物・動体・魚眼外周の扱いはextractの人手確認で明示する。複数素材でのSAM自動追跡は今回追加していない。既存単一OSVのSAM経路は維持している。

```text
masks/
  front/cam0/00000.png
  front/cam1/00000.png
  left/cam0/00000.png
  left/cam1/00000.png
  ...
```

抽出後の画像と同じ寸法の、不透明な白黒二値PNGに限定する。**白255＝保持、黒0＝除外**。conventionに`white-keep`を必ず指定する。黒保持やalpha方式、色付き・中間値は拒否する。マスク画像の内容が逆転しているかは自動で意味理解できないため、重ね表示を人が確認する。除外率1%未満・95%超は警告とし、画質や網羅性の判定にはしない。

固定版Spirula v2026.9.20の`src/data/DataManager.cpp:decode_mask_into`は外部マスクを1チャンネルで読み、非ゼロを保持へ二値化する。SfMの`src/sfm/core/Mask.h`も0が除外。PNGのalphaとは別の経路である。FS Captureでは曖昧さを避けるため0/255のみ受け付け、反転しない。Spirula学習側の境界オフセット等はエンジン設定に従うため、レポートは取り込みマスクそのものの確認である。

categoriesで`fisheye-boundary`、`person`、`moving-object`、`manual`を記録する。これはマスク集合の作成理由であり、画素ごとの物体分類ではない。元マスクのハッシュを登録し、run maskで新しい試行へコピーして検査する。名前の欠損・余分なPNG・寸法不一致・デコード失敗を拒否する。登録後のマスク変更は新規ジョブが必要。編集データの派生ジョブ化はPR3に残る。

## 実行

```sh
python3 -m fs_capture init-manifest input/manifest.json work/room-02 --spirula /path/to/spirula
python3 -m fs_capture plan work/room-02 extract
python3 -m fs_capture run work/room-02 extract
python3 -m fs_capture report work/room-02
```

reportが表示したHTMLをローカルで開き、採用した全画像・素材ID・フレーム・区間を確認する。確認後にreviewのnoteへ自分の根拠を記録する。以下は記入例で、無確認で流用しない。

```sh
python3 -m fs_capture review work/room-02 extract --result pass --note '全画像の対応・採用区間を確認。人物は取り込みマスクで除外する。証拠: …'
python3 -m fs_capture run work/room-02 mask
python3 -m fs_capture report work/room-02
python3 -m fs_capture review work/room-02 mask --result pass --note '全マスクの重ね表示と静物欠損を確認。証拠: …'
python3 -m fs_capture run work/room-02 sfm
python3 -m fs_capture report work/room-02
python3 -m fs_capture review work/room-02 sfm --result pass --note '登録状態・カメラ配置・経路を確認。証拠: …'
python3 -m fs_capture run work/room-02 train
```

run maskは内部コピーのため外部コマンド列は空で、output/import.jsonに元ハッシュ・作成理由を保存する。失敗は試行ごとに保存し、同じrunコマンドで別試行を作れる。成功済みの段階や登録した入力条件は変更しない。

画像・マスクの全画素デコード検査は新しい複数素材経路で実施する。旧単一動画経路の枚数・名前・サイズ検査は従来どおりで、画素検査まで実施済みとは扱わない。Pythonの追加依存は導入せず、既存のFFmpeg/ffprobeを利用する。開発PCでは8.1.2を使用。CIはUbuntuのffmpegパッケージを明示導入し版をログへ残す。FFmpegの配布条件はビルドに依存するため、同梱する場合は使用バイナリの`ffmpeg -L`・ビルド設定を確認する。ライセンスを変更・再許諾するものではない。

## 内部レポートと撮影チェック

```sh
python3 -m fs_capture capture-check work/room-02 '対象設備の正面' \
  --status NEEDS_CAPTURE --reviewer '担当者名' --note '配管で左側が隠れているため追加の視点が必要'
python3 -m fs_capture report work/room-02
```

CAPTUREDは撮影済み、NEEDS_CAPTUREは追加撮影が必要、NOT_TESTEDは未確認。確認箇所を追加することもでき、担当者・日時・理由・設定ハッシュ・その時点の成果物ハッシュを履歴付きで保存する。これは対象箇所の撮影判断であり、閲覧品質・歩行・納品の承認ではない。

HTMLには採用フレーム別の画像・マスク重ね表示・除外率・SfM登録状態・登録姿勢を載せる。選択外区間は未評価と明示し、採用画像の未登録はMISSING、SfM未完了はNOT_TESTEDとする。数値や一覧から視差の十分さを自動合格しない。ブレ・重複をAIで判定する機能はない。

レポートは生成時点のスナップショットで、再生成するたび別フォルダへ保存する。縮小画像を埋め込むため外部CDNは不要だが、オフライン納品の受入試験をした意味ではない。人物・内部情報を含む**内部専用**の資料であり、そのまま顧客へ渡すパッケージではない。顧客向けビューアや用途別QA・配布データは別の変更単位とする。
