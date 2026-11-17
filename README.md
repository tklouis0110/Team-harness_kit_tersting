# team-harness-kit（テンプレート）

経営企画メンバーが Claude Code でデータ整備・集計・計算・小さな自動化を回すための最小キット。上長の vault ＋ project_template と同じ分け方をしている。

| 置き場所 | 中身 |
|---|---|
| **team vault**（全員が clone する共有の vault） | 指示書の正本（プロジェクトごとの ODSC・タスクの仕様・検査・手順・経過・記録・学び・上長の FB）と、共有の知識 `AI/` |
| **プロジェクトのリポジトリ**（このテンプレートから作る） | コードのみ（ハーネス・プログラム・取得データ・成果物）。`CLAUDE.md` は vault を指す道しるべ |

**1 プロジェクト = 1 つの ODSC（目的のまとまり）= 1 リポジトリ。** 新しい目的の仕事を始めるたびにリポジトリを 1 つ作る。計算のたびには作らない。

## PC ごとに 1 回

1. Python 3.10 以上と Git を入れる。
2. 作業用のフォルダ（例: `C:\Dev\GitHub\`）に team vault を **`team-vault` という名前で** clone する。
3. Excel 様式のプラグイン: `/plugin marketplace add tklouis0110/excel-format` → `/plugin install meltec-excel@excel-format`

## 新しいプロジェクト（ODSC）を始めるとき

1. GitHub でこのテンプレートの「Use this template」→ 自分のリポジトリを作る。**名前がプロジェクト名になる**（例: `fy26h2-fixed-cost`。他と重ならない名前）。上長を共同作業者に追加する。
2. `team-vault` と**同じフォルダの中に並べて** clone する（並べてあれば場所の設定は要らない）。
3. `.env.example` を `.env` に写し、上長から渡された読み取り専用の接続先を入れる。
4. そのフォルダを Claude Code で開き、**「始める」** と言う。AI が氏名と記号を聞いて、team vault にこのプロジェクトの指示書フォルダを作り、
   ODSC（何のために・何を渡すか・何が言えたら成功か・誰が使うか）を順に聞く。決めたらロックされる。

## 毎回

- 「タスクを始める。9 月のタイ工場の固定費を科目別に集計したい」→ AI が仕様・検査・手順を書く → **あなたが読んで決定** → 手順ごとに実装と確認 → プログラムが合否判定 → 別の AI が点検 → 記録。
- 終えるとき（`/clear` の前も）: AI が「次にやること」を残して保存する。再開は **「再開」**。
- 上長の FB があると、会話の最初に AI が知らせる。対応すると記録される。
- 途中で「ここも見たい」が出たら、今回の目的に必要かを AI が確かめる。必要でなければ「見送り」として残り、上長が次の仕事にするか決める。

困ったら `python harness/hk.py status`。エラー文はそのまま Claude Code に貼る。動作確認は `python harness/hk.py selftest`。

## 上長向け

- 立ち上げ: このフォルダを GitHub の private リポジトリにして Template repository に設定。team vault のリポジトリを作りメンバーを追加。DB の読み取り専用ロールと excel-format の読み取り権限を渡す。
- FB: team vault の `projects/<プロジェクト名>/FEEDBACK.md` に書いて push。裁定は `AI/Decisions/RULES.md`。共有の知識への蒸留（`LEARNINGS.md` → `AI/`）は上長側だけが行う。
- キットの変更はここで行い `VERSION` を上げる。メンバーは各リポジトリで `python harness/hk.py update`（このテンプレートの clone が隣にある前提）。
- 横断の集計: `python harness/hk.py stats`。監査: メンバーのリポジトリを clone して `python harness/hk.py audit <ID> --out <個人 vault の監査フォルダ>`。
