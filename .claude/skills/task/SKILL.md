---
name: task
description: team vault の指示書（1 プロジェクト = 1 ODSC）に従って、データ整備・集計・計算・小さな自動化（PoC）を進める。プロジェクトの準備（vault に指示書フォルダを作る）、ODSC の聞き取りとロック、タスクの設計→決定→手順ごとの実装→Eval→独立レビュー→記録、上長の FB への対応、会話の終わりの保存と再開。「始める」「タスクを始める」「〜を集計して」「〜を自動化したい」「再開」「続き」と言われたら使う。
---

# task — vault の指示書のもとでタスクを回す

コマンドはすべてこのリポジトリの直下で実行する。`<ID>` は `MZ-003` のようなタスク番号。
**指示書は team vault 側、コードはこのリポジトリ側。** 場所は `python harness/hk.py where [<ID>]` が出す。パスを推測しない。

## ⓪ 会話の始まり（毎回）

`python harness/hk.py resume` を実行して読む。前の会話の内容を覚えている前提で進めない。
- 「このリポジトリは未登録」「指示書フォルダがありません」→ Ⓟ へ。
- 「team vault が見つかりません」→ 何もせず止まり、人に場所を尋ねる。
- 上長の FB が出た → 最初に読み、人と対応を決めて実行し、`python harness/hk.py fb ack "対応した内容"`。
- ODSC が未ロック → Ⓐ へ。
- 「次にやること」や未完了タスクがある → 人に一言確かめて、そこから続ける。
- どれも無い → 新しいタスク（①）。

## Ⓟ プロジェクトの準備（新しいリポジトリで 1 回だけ）

人に 2 つ聞く: 氏名と、タスク番号の頭に付ける英大文字 2〜4 字の記号（例: 水上 → MZ）。
`python harness/hk.py init --name <氏名> --code <記号>`
team vault の `projects/<このフォルダ名>/` に指示書フォルダ（ODSC.md・FEEDBACK.md・LEARNINGS.md）が雛形から作られる。続けて Ⓐ へ。

## Ⓐ ODSC を決める（プロジェクトで 1 回だけ）

`python harness/hk.py where` が出す ODSC.md（vault 側）に、依頼の言葉をそのまま写し、**決まった順で** 聞く（1 回に 1 つ）:
1. **Objective** — これは何のためか。出来上がったら、誰が何を決める／何ができるようになるか。
2. **Deliverable** — 何を・どの形で・いつまでに渡すか。
3. **Success Criteria** — 利用者から見て、何が言えたら成功か。SC1, SC2 … と番号を付ける。
4. **Customer** — 依頼者は誰か。実際に使う人が別にいるか。成果物をどう使うか。

答えが「分からない」なら推測で埋めない。Objective と Customer が分からないままなら、先へ進まず上長に聞くよう伝える。
書けたら全文を人に見せ（Review）、指摘を反映して「見た人・指摘と直した点・決定」を書く。
**「この内容で進める」という言葉をもらってから** `python harness/hk.py odsc lock`、続けて `python harness/hk.py save "ODSC を決定。最初のタスクを作る"`。

このプロジェクトに入れるのは、この ODSC のための仕事だけ。別の目的の依頼が来たら、新しいリポジトリ（テンプレートから作る）を勧める。

## ① タスクの設計

1. どの Success Criteria のためのタスクかを確かめる。**どれにも当たらないなら、このリポジトリの仕事ではない**（park するか、別のリポジトリ）。
2. 種類を決める: 集計・計算・編集 → `calc` ／ 手作業の自動化 → `auto`。
   `python harness/hk.py new "タスク名" --kind calc` → 指示書（vault）とコード（ここ）の場所が表示される。以後 `python harness/hk.py where <ID>`。
3. `python harness/hk.py rules` で上長の裁定を読み、当たる番号を控える。
4. 出どころと規則（calc）／いまの手作業と PoC の範囲（auto）を聞く。テーブルやファイルの場所が分からなければ Explore サブエージェントに調べさせる。
5. Excel の成果物があるなら、meltec-excel の様式一覧から合う様式を選び、様式名と版を仕様に書く。合うものが無ければ「要確認」。
6. **`designer` サブエージェント**（推論寄りのモデル）に、聞き取った内容の要約と、`where <ID>` が出した **タスクの指示書フォルダ（vault 側）** のパスを渡し、spec.md・checks.json・plan.json を 1 回で固めさせる。呼ぶのは 1 タスクに 1 回。
   呼べたら `python harness/hk.py note <ID> opus --type design`、利用枠で呼べなければメインが自分で書いて `... note <ID> sonnet --type design`。
7. 検査の最低限 — `calc`: 取得が空でない／キーが重複しない／合計が出どころと合う。
   `auto`: 見本の入力で動かした出力が、人が出した正しい出力と一致する（`reconcile` か `command`）。数式入り Excel は meltec-excel の再計算・検算を `command` で呼ぶ。
   原本ファイルを使うなら `sources_unchanged` を入れる。

## ② 決定（タスクのロック）

次の順で人に見せる（短く）: 対応する Success Criteria → 規則 → 検査 → 手順の分解と見積の合計 → 要確認。
**「この内容で実行する」という言葉をもらうまで実装しない。** 要確認が規則や検査を変えうるなら、答えが出るまでロックしない。
決定したら `python harness/hk.py lock <ID>`。

## ③ 手順ごとに実装と確認

plan.json の手順を 1 つずつ進める。1 手順の流れ:
1. `python harness/hk.py step <ID> S1 start`（ODSC と見積の残りが表示される）
2. kind が `calc` なら `analyst`、`auto` なら `automator` サブエージェントに、タスクの指示書フォルダ（vault 側）のパス、コードのフォルダ（ここの `tasks/<ID>/`）、**手順 ID** だけを渡して任せる（会話の経緯は渡さない。仕様が正）
3. `python harness/hk.py step <ID> S1 done`
4. 結果を 2〜3 行で人に見せ、次へ進んでよいかを聞く

人が直しを求めたら、その手順を `start` し直してやり直す。人が直しを求めたら、その手順を `start` し直してやり直す。
試したこと・直したこと・人が決めたことは、その都度 1 行で残す:
`python harness/hk.py note <ID> "何をして何が起きたか" --type try|fix|decide|block`
**範囲の守り方**: `step start` と `resume` は毎回 ODSC を表示する。手順を始める前に読み、これからやることがどの Success Criteria のためかを言えること。
途中で論点が増えたら CLAUDE.md の 4 に従って「必要／park／目的の変更」に分ける。分析では park が普通に出る。出ないまま時間だけ延びるほうが異常。
`!! 見積の 1.5 倍を超えました` が出たら先へ進まず、続ける・ここで閉じる・残りを別タスクに割る、を人に聞いて `note --type decide` に残す。
会話を終えるとき・止まるとき・人が `/clear` と言ったときは、必ず `python harness/hk.py save "次にやること"` を実行してから終える（現在地が vault と GitHub に残る）。

担当が使うもの:
- データ取得: `python harness/hk.py fetch <ID> <名前> --sql-file tasks/<ID>/src/<名前>.sql`
- 原本ファイル: `python harness/hk.py source <ID> <パス>`
- プログラムはこのリポジトリの `tasks/<ID>/src/`（作り直しの入口 `src/run.py` が必須）、成果物は `tasks/<ID>/out/`（置き方は `formats/OUTPUT.md`）。指示書のフォルダにコードやデータを置かない

## ④ Eval

verify の手順で `python harness/hk.py eval <ID>` を実行。FAIL なら不合格の行だけを同じ担当（analyst / automator）に渡して直させる。
直すたびに `note --type fix` を残す。**同じ検査が 2 回直しても FAIL なら止まって人に報告。** 検査のほうが誤りだと思っても自分で変えず、理由を伝える。

## ⑤ 独立レビュー

PASS したら `reviewer` サブエージェントに、タスクの指示書フォルダ（vault 側）とコードのフォルダのパスだけを渡す。返ってきた全文を **指示書フォルダの** `review.md` に保存する
（1 行目は `PASS` か `FAIL`）。FAIL なら指摘を人に見せ、直すか・仕様を変えるかを決めてもらう。
「要確認」に上長が答えた内容が今後のタスクにも当たりそうなら、裁定への追加を上長に提案する文を記録の学びに入れる（自分では書かない）。

## ⑥ 記録と共有

人に「今回の学び」を 1〜2 文で聞く（次に同じ作業をする人が助かること。無ければ AI が案を出して選んでもらう）。
`python harness/hk.py close <ID> --learning "学び"`
記録・学び・実績が team vault の `projects/<プロジェクト名>/` に書かれ、vault とこのリポジトリが GitHub に送られる。送信に失敗したら表示された文を人に伝える。
途中でやめるとき: `python harness/hk.py close <ID> --abandon "理由"`

## 検査の type

| type | 確かめること | 主な項目 |
|---|---|---|
| row_count | 行数 | file, equals / min / max |
| not_null | 空欄がない | file, columns |
| unique | 重複がない | file, columns |
| allowed_values | 決まった値だけ | file, column, values |
| range | 数値の範囲 | file, column, min, max |
| sum_equals | 合計が期待値／別ファイルの合計と一致 | file, column, expected または other_file + other_column, tolerance |
| reconcile | キーごとの合計が 2 つのファイルで一致 | file_a, column_a, file_b, column_b, key, tolerance |
| foreign_key | 参照先に存在する | file, column, ref_file, ref_column |
| file_exists | 成果物がある | path |
| sources_unchanged | 登録した原本が変わっていない | （なし） |
| command | プログラムが正常終了（自動化のテスト用） | cmd |

ファイルは CSV（タスクフォルダからの相対パス）。Excel の成果物は、主要な数字を `out/summary.csv` にも出して検査する。
すべての検査に `id` と `why`（なぜ必要か）を書く。
