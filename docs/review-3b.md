# PR3B: 修正候補の作成と比較

2026-09-25。開始HEADは`2285a679dcad398150e4da244a79aaab706bc730`、未コミット差分なし。fetch後もorigin/mainは同じ。`feature/review-3b`で作業。既存のPR3Aを再実装せず、元ジョブ・成功成果物・旧8視点FAILを保持する。

## 変更単位1: 比較と記録の障害

- マスク設定のないschema 1/2のジョブはmask段階なしでプレビューできる。未適用表示、overlay無効、maskUrl=null。指定ありの欠損・破損・未承認は拒否する。
- カメラ変更・移動・見回し・ホームで古い判定を未確認へ戻す。対象bundle/視点/投影/描画設定/判定を確認操作へ結び付け、新しい確認なしに書き出せない。担当者・用途・理由は下書きとして保持する。
- 過去記録を復元しても新しいPASSにはしない。過去の判定を別表示し、新記録には復元元IDを付ける。
- 「保存用JSON作成」「ブラウザへ保存要求」「CLI取り込み済み」を区別。配信は読み取り専用のまま。

変更前はPythonのマスクなし1件が拒否され、実app.jsをDOM/画像/GPUのテストダブルで動かした7件が失敗。変更後はPython73件とJS11件が成功、fail/skip0（JSはPythonからの呼び出しと重複）。FFmpeg必須10件も成功し、Python全体と重複する。記録は`work/review-3b/{baseline*,red-*,part1-*}`。

既存の199,952 Gaussian実モデルから、新UI入りの独立プレビューを標準CLIで生成しハッシュ検査を通した。保存先は`work/review-3b/part1-real-preview.json`。旧プレビューや旧QAは編集していない。

**実ブラウザのJSON保存→実ファイル→CLI取り込み→再起動→復元はNOT_TESTED。** Browserスキルの接続とトラブルシューティングを実行したが、利用可能ブラウザは空だった。別の自動化手段で迂回せず、DOM合成試験を実ブラウザ試験として数えない。今回、新しい画質判定も行っていない。

## 変更単位2: 学習だけの派生と同一SfMのA/B

2026-09-26に継続。変更単位1のローカルコミットは`5d2a8d4`。

`derive PARENT CHILD --iterations N`は反復数だけを変更する。入力・実行ファイル・承認済み上流を検査し、extract/mask/sfmを独立コピー。親試行とハッシュ、段階別の依存キー、再利用であり再実行していないことを記録する。設定全体のhashの一致だけに依存せず、反復数を除いた入力・抽出・マスク条件・エンジン・上流成果物の一致を検査する。親の撮影確認やモデルの画質QAはコピーしない。旧成功時hashなし、コピー中断、親・子の改変、未承認、同じ反復数は拒否する。

`preview-compare A B OUTPUT`は同じSfMと抽出/マスク成果物、カメラ・投影・変換・描画条件を要求する。別SfMはカメラの数値と恒等変換が同じでも拒否する。新パッケージで候補を切り替えると視点を保ち、判定を未確認に戻す。読み込み失敗では前のモデルを候補Bとして保存できない。比較QAは候補ID・モデルhash・元bundleにも結び付ける。過去QAは視点の再利用だけが可能で、旧PASS/FAIL・証拠は新判定へ継承しない。

### 実素材の処理結果

| 項目 | 実施結果 |
|---|---|
| 親 | `work/review-2/five-fixed-job`。10,000反復、199,952 Gaussian、旧8視点はFAILのまま |
| 派生 | `work/review-3b/train-20000`。固定5素材・10画像、20,000反復、197,937 Gaussian |
| 再利用 | extract/mask/sfmの全成果物が親の成功時hashと一致。独立コピー。抽出・マスク・SfMの外部処理は0回 |
| 親の保全 | 実行前後のjob.json・state.json・全成功成果物hashが一致 |
| 実行環境 | 既存M3 Max開発PC、Python3.14.6、FFmpeg8.1.2、Spirula2026.9.20。新規ダウンロードなし |
| GPU学習 | 標準CLIの新train試行で完了。学習ログは580.255秒。待機・素材検査・人手を含む制作時間ではない |
| 最終PLY SHA-256 | `bfabe754a571c5e57d9c75a940b7b18def918f38d22945399299a3e8473c11d0` |
| 学習条件差 | num_iterationsとsteps_per_saveが10,000→20,000。その他の差はコピー先入力と出力のパス。素材・SfM・マスクの内容、表示方式は同一。GPU学習の非決定性は分離していない |
| A/Bパッケージ | `work/review-3b/compare-10k-20k`。旧8視点を検証位置として登録、新QAは0件。全視点の新判定はNOT_TESTED |
| 配信 | 実HTTPで両PLYの受信hash、8視点、新QA空を確認。POST拒否、対象外path拒否、異なるOrigin拒否。ブラウザ試験ではない |
| 実ブラウザ・画質 | NOT_TESTED。接続を再確認しても利用可能なブラウザなし。旧FAILを新モデルへ流用せず、改善とも判定しない |

最初のtrain試行はsandbox内でMetal/Vulkanが使えず失敗した。通常権限で再試行して成功し、失敗試行も`attempts/train-22acc7715b21`に保持した。HTTP検査もsandboxのlocalhost接続拒否後に通常権限で再試行した。OSの安全設定は変更していない。

### 自動試験と再現

最終ローカル結果は**Python84件成功、失敗0、skip0**。JSは**13件成功、失敗0、skip0**（投影4、DOM UI9）。JSはPythonの2件からも起動するため単純合計しない。FFmpeg必須は**11件成功・skip0**で全体Python試験と重複する。GitHub CIは今回未実行で、workflowのJS対象とFFmpeg必須対象を更新した。

新たに確認した内容は独立コピー、親不変、schema 1/2互換、コピー失敗の拒否、学習だけの実行、改変・未承認・旧snapshot拒否、別SfM/投影拒否、A/B候補取り違え拒否、QA非継承、同一視点保持、モデル読み込み失敗時の保存禁止。DOM試験は実app.jsを使用するがGPU/DOM/画像はテストダブルであり、ブラウザ受入ではない。

途中の複数素材テストは、一時フォルダの`/var`エイリアスを手作りSfMのoutputへ記録してリンク検査に拒否された。fixtureを標準runと同じresolve済みパスに直し、製品のリンク検査は緩和していない。初回はテストクラスのimportによって既存8件が重複実行されたため、モジュールimportへ修正した。最終84件には重複を含めない。途中ログは削除していない。

```sh
python3 -m unittest discover -s tests -v
node --test tests/*.test.mjs
python3 scripts/test_ffmpeg_integration.py
python3 -m fs_capture derive work/review-2/five-fixed-job work/review-3b/train-20000 --iterations 20000
python3 -m fs_capture run work/review-3b/train-20000 train
python3 -m fs_capture preview-serve work/review-3b/compare-10k-20k --port 8768
```

既存の派生先には再作成しない。再試験する際は新しいジョブ名を指定する。確認UIは`http://127.0.0.1:8768/`、localhost限定。

ローカルの証拠は`work/review-3b/`内の`final-tests.txt`、`final-js.txt`、`final-ffmpeg.txt`、`derive-real.json`、`train-real-retry.json`、`training-config-diff.json`、`compare-command.json`、`real-verification.json`、`http-smoke.json`。実素材・モデル・プレビュー・証拠をGitへ追加していない。

## 制作者にとっての到達点と残る条件

反復数の変更は2コマンドで学習へ進める。抽出・マスク取り込み・SfMの再実行と再承認を省け、親を保ったまま比較候補ができる。8視点も手で座標を転記せず再利用できる。ただしコピー・ハッシュ照合・再検査の時間は発生し、今回の値から制作速度の一般的な向上率を主張しない。

**PR3B-1の実ブラウザ保存・復元受入と、新A/B画面のGPU描画受入は未完了。** 次は接続済みブラウザで、両候補を同じ8視点から確認し、JSONの実ファイル保存→CLI取り込み→サーバ再起動→視点/候補復元を通す。その後、設備・周辺形状を説明できるかを判定する。学習画像PSNRやGaussian数を根拠に新モデルを画質PASSにしない。

マスク変更の派生、別SfMの位置合わせ、移動撮影・保留視点の比較、対象顧客に近い1室での検証、別担当者の操作、顧客パッケージ、公開審査・納品・別PC/通信遮断は後続。現時点で顧客へ品質を約束できる成果物になったとは扱わない。

## 受入に向けた復旧修正（2026-09-26）

基準HEADは`44303d53f8494dc3052051a04a1553498e64c885`。`feature/review-3b`、差分なしから着手し、fetch後もoriginの同ブランチと同じだった。mainは`2285a67`。今回はpush・mergeを行わない。

### 追記: 公開後のCI

上記の「CI未実行」はローカル実装完了時点の記録。[公開後のrun 36204191151](https://github.com/Mitsuru-Ito/fs-capture/actions/runs/36204191151)は`44303d5`についてsuccessで、今回APIとログを取得して確認した。Python3.10/3.12/3.14はいずれも84件中73成功・11skip・失敗0、直接JS試験は13成功・失敗/skip0。別の必須FFmpegジョブは11成功・skip0。このCIは以下の復旧修正を含まない。

### 再現と修正

初回モデル／参照画像の失敗で、イベント登録より前にbootが中断する問題を、正規repoの実app.jsで再現した。追加した最初の5条件を含むUI試験は変更前14件中10成功・4失敗。失敗は初回モデル、初回画像、B切替の再試行、初期化待ちの操作ガード。正常起動は成功。一つの初期化・復旧経路の問題として扱う。

- UIイベントを同期的に一度だけ登録し、データ／ランタイム初期化と、モデル／画像の再試行を分離した。
- 失敗工程を表示し、モデル／画像には再試行、設定・QA・ランタイムには明示的なページ再読み込みを用意した。未初期化のカメラにはアクセスさせない。
- 読み込み・再試行を直列化。初回失敗後の選択、再試行連打、画像待ちの候補変更でも重複ロードしない。画像だけの失敗はモデルを再確保しない。
- 読み込み中・失敗中は保存不能。失敗候補のキャンバスは隠し、旧候補を次候補として保存しない。再試行後も判定を未確認に戻す。

最終DOM試験は18成功。追加9条件は正常起動、初回モデル／画像の復旧、Bの復旧、初期化待ち、再試行の重複、画像待ち、QA取得失敗、ランタイム失敗。既存9条件も維持。JSのGPU・DOM・画像はテストダブルで、リソースの実測やブラウザ受入の証拠ではない。

### 今回実施した範囲

開発PCはmacOS26.5.1／M3 Max、Python3.14.6、Node24.3.0。`python3 -m unittest discover -s tests -v`は84成功・失敗/skip0、`node --test tests/*.test.mjs`は22成功・失敗/skip0（投影4＋UI18）、`python3 scripts/test_ffmpeg_integration.py`は11成功・失敗/skip0。Python内のNode呼び出し、FFmpeg部分集合と重複するため合算しない。ブラウザの接続が切れており、Browserの復旧案内に沿った列挙でも0件だった。別の制御経路へ迂回していない。

既存10k/20kモデルから修正UI入りの新プレビューと比較パッケージを標準CLIで作成。元job/state、成功成果物、旧8視点QA、旧比較パッケージが変更されていないことをSHA-256で照合した。新しい学習は実施していない。

| 項目 | 今回の結果 |
|---|---|
| 修正UIと既存2モデルのパッケージ生成・ハッシュ照合 | PASS |
| 実HTTP、サーバ停止／再起動後の配信 | PASS。2つのPLYの受信hash、8視点、QA空、POST501・対象外path404・異なるOrigin403 |
| 実ブラウザ／WASM・WebGL2描画／10回切替・所要時間・メモリ | NOT_TESTED |
| ブラウザから実ファイル保存／PNG内容確認／再起動後の画面復元 | NOT_TESTED。HTTP再起動を画面復元と読み替えない |
| 候補A・10k | 過去8視点のFAILを保持。新画面での再判定はNOT_TESTED |
| 候補B・20k | NOT_TESTED。旧判定を流用せず、今回の画質確認者・証拠はなし |
| PR3Bの利用者向け受入／顧客納品 | 未完了 |

新パッケージは`work/review-3b-acceptance/compare-10k-20k`、URLは`http://127.0.0.1:8769/`。bundle IDは`bc962d0f2e25bb9051779b9168b546fba65874d4bdd67d24592d51e30dd49b62`。既存8768のパッケージには修正を上書きしていない。

証拠は同じ`work/review-3b-acceptance/`内の`baseline-*`、`red-ui.txt`、`green-ui.txt`、`final-*`、`ci-44303d5.log`、`before.json`、`verification.json`、`preview-a.json`、`preview-b.json`、`compare-command.json`、`http-before-restart.json`、`http-after-restart.json`。HTTP検査ではsandboxがlocalhost接続を拒否したため、許可された通常権限で再実行した。OSの安全設定は変更していない。実素材・証拠・ローカルパス入りログはGitへ追加していない。

制作者は初回の一時的な読み込み失敗から画面内の操作で復旧できる実装になった。実ブラウザでの有効性は受入待ちで、確認時間や生産性の改善量は未計測。[短い受入手順と記録欄](preview.md#pr3bの実ブラウザ受入票1端末)を既存資料へ追加した。

次の分岐は**受入待ち**。A（20kを採用）／B（共通の画質問題から追加撮影へ）のどちらもまだ選べない。Cの表示・保存経路を実ブラウザで確認し、失敗すれば最小原因を直す。旧2モデルを評価できるまで、学習量追加・マスク派生・圧縮・納品機能へ拡張しない。
