# 1室・1設備群の現場PoC

2026-09-26。需要・効果・価格は未確認の仮説。ブラウザ操作の成立と、用途に必要な画質・顧客検収・投資価値は別に扱う。旧固定素材の10k/20kは各8視点FAILであり、反復数を増やすだけの追加学習は今回行わない。

## 撮影前に決めること

現場責任者と、説明に必要な5〜10箇所程度（提案数で、合格閾値ではない）を設備名で定義する。入口、設備正面、周辺の位置関係、特徴点などについて「何が見えればよいか」を事前に合意する。撮影日・版・担当者を記録する。人物、表示物、業務情報、立入範囲の扱いは現場の許可に従う。確認作業は安全な待機場所で行い、稼働設備へ接近したり、撮影しながら危険箇所を移動したりしない。この手順は現場の安全手順を代替しない。

静的な1室で、60〜120秒程度の短い移動撮影から試す（開始案で品質保証ではない）。同じ場所で回転するだけでなく位置を変え、必要形状を異なる方向から観測する。人物を重要形状へ重ねない。銘板・微細部分は別撮り写真で確認する。撮影・マスク・投影・表示・学習設定の差と変更理由を残す。複数条件を変えた初回は実現性試験であり、一要因の効果とはしない。

**評価専用素材は別撮影として先に確保する。** 初版は元動画単位の分離で、同時刻の両レンズ、隣接フレームをまとめて学習側または評価側にする。`evaluationSources`は学習コマンドへ渡さず、同じパス・同じ内容の複製を拒否する。画像を再圧縮したものや、動画から別名で切り出した画像の出自はハッシュだけで検出できないため、担当者が独立撮影であることを確認する。評価写真をマスク調整や条件選択へ流用しない。正確な位置・校正がない参考写真からPSNRや新規視点誤差を算出しない。

## 最小操作

以下はパス・案件名を置き換える操作例であり、実撮影を実施した記録ではない。

```sh
python3 -m fs_capture capture-manifest work/room-manifest.json \
  --source /path/to/moving.OSV --seconds 60 --sample-fps 1 \
  --evaluation-source /path/to/independent-reference.jpg \
  --target 入口 --target 設備正面 --target 周辺配置 --target 背面 --target 特徴点
python3 -m fs_capture init-manifest work/room-manifest.json work/room-pilot --spirula /path/to/spirula
python3 -m fs_capture field-plan work/room-pilot \
  --case pilot-01 --site room-01 --equipment equipment-01 \
  --purpose '設備の位置と周辺形状の理解' --date 2026-09-26 --revision v1 \
  --reviewer 担当者 --target 入口 --target 設備正面 --target 周辺配置 --target 背面 --target 特徴点
python3 -m fs_capture preflight work/room-pilot --stage extract
python3 -m fs_capture run work/room-pilot extract
python3 -m fs_capture report work/room-pilot
python3 -m fs_capture field-check work/room-pilot 設備正面 \
  --status CAPTURED --capture capture_01 --lens cam0 \
  --visibility OBSERVED --reviewer 担当者 --note '採用画像を確認。文字は別写真で確認する'
```

`field-plan.html`は印刷できる内部確認表。画像レポートからも参照できる。`--capture`はその素材・レンズの採用画像**全体**を根拠に指定するため、未確認の画像まで確認済みとしない。個別指定は従来の`--reference extract:images/...`を使う。別撮り根拠写真は`--photo /path/to/detail.jpg`で登録し、可読性とハッシュを記録する。内部用の根拠写真は公開承認を意味しない。

撮影状態はCAPTURED／NOT_CAPTURED／OCCLUDED／NEEDS_CAPTURE／NOT_TESTED。CAPTUREDには存在する根拠が必要。可視性はUNOBSERVED（未観測）、OCCLUDED（遮蔽）、PRIVACY_HIDDEN（公開上の非表示）、RECONSTRUCTION_UNCERTAIN（復元不確か）、OBSERVED（観測）を区別する。公開状態はNOT_TESTED／RESTRICTED／APPROVEDで、対象写真・箇所に対する担当者の確認だけを表し、モデル全体の納品許可ではない。見えないことを「存在しない」「安全」「異常なし」と解釈しない。

追加の独立評価素材は`field-evaluation JOB SOURCE`で登録できる。初期manifestに登録した評価素材もrun時にハッシュ照合する。既存ジョブの設定・成功成果物は編集しない。確認表は元ジョブの設定ハッシュ、capture-checkの履歴は成果物・根拠・確認者・日時と結び付く。

## 実行予算

`preflight`のN(N−1)/2は全画像対の組合せ数で、エンジンの実処理数や時間ではない。暫定上限300画像・44,850候補、空き1GiBは小規模試験用の保守的な運用値で、実測性能に基づく保証ではない。`budget JOB --max-images N --max-pairs N --min-free-bytes N --reason 理由`で明示変更し、旧判断は履歴へ残す。runは実行前に再診断し、予算と結果を試行に保存する。exhaustiveを変更しない。

画像数不明の旧単一動画は既定では停止する。`budget`の`--allow-unknown-extraction`と理由を明示した場合だけ抽出して枚数を確定できる（上限内を保証する許可ではない）。ただしSfM・学習には画像数不明のまま進めない。`plannedCopyBytes`は指定run段階のコピー（元動画は参照、手修正マスクはコピー）だけ。`--copy-upstream`を付けると成功時ハッシュを確認してtrain-only派生の上流コピー量を算出する（空き容量は元ジョブのファイルシステム。同一ボリュームの派生先を想定）。previewのコピー、最終PLY容量、ピークGPU使用量、所要時間はこの診断の保証範囲外。空き容量の余裕は出力容量の保証ではない。

## QA再取り込み

同じschema 1/2書き出しは、確認日時・確認者・候補・視点・理由・PNGを含む内容で識別する。`canonical-json-v1`はキー順を整えたJSON全体をハッシュ化し、PNGだけでは判定しない。日時や確認者が変わった新しい観察は別記録。既存画面の書き出し形式を変えず、まずこの最小方式を採用した。

外部の記録元が`observationId`と`exportId`を両方指定した場合は、観察と書き出しの再試行を区別する。同じ観察の再書き出しは既存記録を返し、exportの別名だけ追記する。同じIDで内容が変われば競合として拒否する。旧記録も読み取り専用で内容を再構成し、既存の重複記録を自動削除・統合しない。PNG検査後、証拠を書き、最後に原子的なJSON書き込みを完了印とする。途中失敗で残ったPNGは取り込み済みとみなさない。

## 業務比較と工数

人が用途に必要な画質を認めた後に、同じ設備名・説明・写真への導線を揃えた三条件を比較する：現行資料／写真・360資料／3D成果物。3Dの効果と資料整理の効果を混同しない。

`field-plan`が作る空の`task-observations.csv`を使い、各試行で、匿名の参加者ID、提示順、条件、対象設備、タスク（設備探索／周囲の説明／正しい根拠への到達）、正答・誤認・未確認、秒数、支援回数、失敗理由を表に残す。未実施条件はNOT_TESTED。速くても誤答なら成功にしない。異なるが難度の近い対象と提示順の入替えを検討する。最初は1室、その後に制作者2名×3環境・利用者6〜12名程度という探索案であり、母集団の効果・安全事故減少・市場需要を証明するものではない。

```sh
python3 -m fs_capture field-log work/room-pilot --activity マスク修正 \
  --kind human-active --minutes 15 --operator 担当者 --note '開始終了を手動計測'
python3 -m fs_capture field-log work/room-pilot --activity 学習 \
  --kind machine-wait --minutes 10 --operator 担当者 --note '処理ログの実測値'
```

数値は操作例で実績ではない。撮影、出張、許可調整、選別、マスク、コピー、検査、学習、確認、再撮影、更新、納品支援を失敗分も含めて記録する。並行する機械待ちを実働へ加算しない。runは新規試行の事前検査・実行・検証＋ハッシュ時間を分離する。既存試行へ時間を後付けしない。

顧客側の年間時間便益は利用回数×短縮時間×時間単価。重複しない実証便益を加え、更新・運用費を差し引く。正の純便益の場合のみ初期費用÷年間純便益で単純回収年数を計算する。空いた時間と実際の支出削減は別。提供側は売上−撮影・出張・制作・修正・再撮影・計算・ライセンス・支援の原価を集計し、初回構築と反復提供を分ける。未計測値は空欄とし、本実装は価格・需要・ROIを生成しない。

次へ進む条件は、人の用途確認、正確さを落とさない業務改善、開発者の常時介入なしの運用、総費用を上回る再利用便益。数値閾値は利用者・決裁者と事前に定める。成立しなければ撮影・マスク・投影等を切り分け、360資料の代替も検討する。顧客の合意なく3D契約を別形式へ変更しない。内部プレビューの顧客配布は行わない。
