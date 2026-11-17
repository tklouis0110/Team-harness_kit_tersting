#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""hk — team harness kit の唯一の実行体（標準ライブラリのみ）。

構成（上長の vault / project_template と同じ分け方）:
  team vault の projects/<プロジェクト名>/  … 指示書の正本（ODSC・仕様・検査・手順・経過・記録・学び・FB）
  このリポジトリ（プロジェクト名と同じ名前）  … コードのみ（ハーネス・プログラム・取得データ・成果物）
1 プロジェクト = 1 つの ODSC。その中でタスクを何本も回す。AI はこのスクリプトを呼ぶだけで、合否を自分で判定しない。

  python harness/hk.py init --name 氏名 --code MZ     （新しいリポジトリで 1 回。vault に指示書フォルダを作る）
  python harness/hk.py where [<ID>]                    （指示書とコードの場所を表示）
  python harness/hk.py odsc lock | odsc show
  python harness/hk.py new "タスク名" [--kind calc|auto]
  python harness/hk.py lock  <ID>
  python harness/hk.py step  <ID> S1 start|done
  python harness/hk.py fetch <ID> <名前> --sql-file tasks/<ID>/src/x.sql
  python harness/hk.py source <ID> <原本ファイル>
  python harness/hk.py note  <ID> "内容" --type try|fix|learn|decide|block|design|park
  python harness/hk.py eval  <ID>
  python harness/hk.py close <ID> --learning "学び"
  python harness/hk.py save "次にやること"             （会話を終える前・/clear の前）
  python harness/hk.py fb show | fb ack "対応した内容"  （上長の FB）
  python harness/hk.py status | resume [<ID>] | rules | stats | update | selftest
  python harness/hk.py audit <ID> [--out 監査.md]      （上長側。取り直し・作り直し・検査し直し）
"""
import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import uuid
from decimal import Decimal, InvalidOperation
from pathlib import Path

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

ROOT = Path(os.environ.get("HK_ROOT") or Path(__file__).resolve().parent.parent)
TEMPLATES = Path(__file__).resolve().parent / "templates"
VAULT_OVERRIDE = None  # selftest 用

SC_RE = r"(?<![A-Za-z0-9])SC\d+(?!\d)"  # 日本語が前後に続いても拾う（\b は使えない）
IDLE_CAP_MIN = 30  # これより長い空きは「作業していない時間」として数えない
OVERRUN = 1.5  # ロック後の作業時間が見積合計のこの倍を超えたら、続けるか・閉じるか・割るかを人に決めさせる
STEP_TYPES = ("fetch", "shape", "calc", "build", "verify")
NOTE_TYPES = ("try", "fix", "learn", "decide", "block", "design", "park")
LOCKED = ("spec.md", "checks.json", "plan.json")
VAULT_MSG = ("team vault が見つかりません。探した場所: このリポジトリの隣の team-vault、.env の TEAM_VAULT。"
             "推測で進めず・新しく作らず、止まって人に場所を尋ねてください")


class HkError(Exception):
    pass


def now():
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def ts(x):
    return dt.datetime.fromisoformat(x)


def rj(p):
    return json.loads(Path(p).read_text(encoding="utf-8"))


def wj(p, obj):
    Path(p).write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def version():
    f = ROOT / "VERSION"
    return f.read_text(encoding="utf-8").strip() if f.exists() else "?"


# ---------------------------------------------------------------- DB（読み取り専用）
FORBIDDEN = r"\b(insert|update|delete|drop|alter|create|truncate|grant|revoke|copy|merge|call|do|vacuum|attach|pragma)\b"


def check_select_only(sql):
    s = re.sub(r"--[^\n]*", " ", sql)
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"'(?:[^']|'')*'", "''", s).strip().rstrip(";").strip()
    if ";" in s:
        raise HkError("SQL は 1 文だけにしてください")
    if not re.match(r"(?is)^(select|with)\b", s):
        raise HkError("SELECT（または WITH ... SELECT）だけ実行できます")
    hit = re.search(FORBIDDEN, s, flags=re.I)
    if hit:
        raise HkError(f"読み取り専用です。使えない語: {hit.group(1)}")


def env(key):
    if os.environ.get(key):
        return os.environ[key]
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(key + "="):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    return None


def connect(source):
    key = "DATABASE_URL" if source == "default" else f"DB_{source.upper()}"
    url = env(key)
    if not url:
        raise HkError(f".env に {key} がありません（.env.example を参照）")
    if url.startswith("sqlite:///"):
        path = url[len("sqlite:///"):]
        if not Path(path).is_absolute():
            path = str(ROOT / path)
        return sqlite3.connect(f"file:{path}?mode=ro", uri=True), key
    if url.startswith(("postgres://", "postgresql://")):
        try:
            import psycopg
        except ImportError:
            raise HkError("PostgreSQL には psycopg が必要です: pip install \"psycopg[binary]\"")
        conn = psycopg.connect(url, connect_timeout=30)
        conn.read_only = True
        return conn, key
    raise HkError(f"{key} の形式が未対応です（sqlite:/// か postgresql://）")


def query_to_csv(sql, source, out):
    check_select_only(sql)
    conn, key = connect(source)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        rows = cur.fetchall()
    finally:
        conn.close()
    with Path(out).open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        w.writerows([["" if v is None else v for v in r] for r in rows])
    return cols, rows, key


def sorted_sha(p):
    """行の並びに依らないハッシュ（ORDER BY の無い SQL でも再取得と比べられる）。"""
    return hashlib.sha256(b"\n".join(sorted(Path(p).read_bytes().splitlines()))).hexdigest()

# ---------------------------------------------------------------- 場所（vault = 指示書の正本 / リポジトリ = コード）
def vault():
    if VAULT_OVERRIDE:
        return VAULT_OVERRIDE if VAULT_OVERRIDE.is_dir() else None
    p = env("TEAM_VAULT")
    v = Path(p) if p else ROOT.parent / "team-vault"
    v = v if v.is_absolute() else ROOT / v
    return v if v.is_dir() else None


def member():
    f = ROOT / "harness" / "member.json"
    if not f.exists():
        raise HkError("このリポジトリは未登録です。先に: python harness/hk.py init --name 氏名 --code XX")
    return rj(f)


def pdir(create=False):
    """vault の中の、このプロジェクトの指示書フォルダ: projects/<このリポジトリのフォルダ名>/"""
    v = vault()
    if not v:
        raise HkError(VAULT_MSG)
    m = member()
    p = v / "projects" / ROOT.name
    rf = p / "REPO.json"
    if rf.exists():
        if rj(rf).get("repo_id") != m.get("repo_id"):
            raise HkError(f"vault に同じ名前の別のプロジェクトがあります（projects/{ROOT.name}、担当 {rj(rf).get('member')}）。"
                          "このリポジトリのフォルダ名を変えてください")
        return p
    if not create:
        raise HkError(f"vault に指示書フォルダがありません（projects/{ROOT.name}）。先に: python harness/hk.py init --name 氏名 --code XX")
    for sub in ("tasks", "records"):
        (p / sub).mkdir(parents=True, exist_ok=True)
    rep = {"{{PROJECT}}": ROOT.name, "{{MEMBER}}": m["name"], "{{DATE}}": now()[:10], "{{VERSION}}": version()}
    for name in ("ODSC.md", "FEEDBACK.md", "LEARNINGS.md"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        for k, val in rep.items():
            text = text.replace(k, val)
        (p / name).write_text(text, encoding="utf-8")
    wj(p / "feedback_ack.json", {"sha256": sha(p / "FEEDBACK.md"), "at": now()})
    wj(rf, {"project": ROOT.name, "member": m["name"], "code": m["code"], "repo_id": m.get("repo_id"),
            "created": now(), "template_version": version()})
    return p


def sdir(tid):
    """タスクの指示書（vault 側）: 仕様・検査・手順・ロック・経過・Eval 結果"""
    d = pdir() / "tasks" / tid
    if not (d / "task.json").exists():
        raise HkError(f"タスク {tid} がありません（vault の projects/{ROOT.name}/tasks/{tid}/）")
    return d


def cdir(tid):
    """タスクのコード（このリポジトリ側）: src/ data/ out/"""
    d = ROOT / "tasks" / tid
    for sub in ("data", "src", "out"):
        (d / sub).mkdir(parents=True, exist_ok=True)
    return d


def safe(d, rel):
    p = (d / rel).resolve()
    if d.resolve() not in p.parents and p != d.resolve():
        raise HkError(f"タスクフォルダの外は参照できません: {rel}")
    return p


def git(*args, cwd=None, timeout=60):
    try:
        return subprocess.run(["git", *args], cwd=cwd or ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 1, "", "時間切れ")


def cmd_where(a):
    p = pdir()
    print(f"指示書フォルダ（正本・vault）: {p}")
    print(f"  ODSC: {p / 'ODSC.md'}")
    print(f"  上長の FB: {p / 'FEEDBACK.md'}（読むだけ）")
    print(f"  学び: {p / 'LEARNINGS.md'}")
    print(f"共有の知識（読むだけ）: {vault() / 'AI'}")
    if a.task:
        print(f"タスク {a.task} の指示書: {sdir(a.task)}  （spec.md / checks.json / plan.json / log.md / review.md）")
        print(f"タスク {a.task} のコード: {cdir(a.task)}  （src/ data/ out/）")
    return 0


# ---------------------------------------------------------------- 時刻の記録
def log_event(sd, event, **kw):
    with (sd / "events.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps({"at": now(), "event": event, **kw}, ensure_ascii=False) + "\n")


def events(sd):
    f = sd / "events.jsonl"
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()] if f.exists() else []


def active_min(times, t0=None, t1=None):
    """イベントの間隔を IDLE_CAP_MIN で頭打ちにして足した作業時間（分）と、会話の回数。"""
    xs = sorted(t for t in times if (t0 is None or t >= t0) and (t1 is None or t <= t1))
    gaps = [(b - a).total_seconds() / 60 for a, b in zip(xs, xs[1:])]
    return round(sum(min(g, IDLE_CAP_MIN) for g in gaps)), 1 + sum(g > IDLE_CAP_MIN for g in gaps)


def step_times(sd, evs, end):
    times = [ts(e["at"]) for e in evs]
    out = []
    for st in rj(sd / "plan.json").get("steps", []) if (sd / "plan.json").exists() else []:
        marks = [(ts(e["at"]), e["state"]) for e in evs if e["event"] == "step" and e.get("step") == st["id"]]
        total, starts, open_at = 0, 0, None
        for t, state in marks:
            if state == "start" and open_at is None:
                open_at, starts = t, starts + 1
            elif state == "done" and open_at is not None:
                total += active_min(times, open_at, t)[0]; open_at = None
        if open_at is not None:
            total += active_min(times, open_at, end)[0]
        out.append({**st, "actual_min": total if starts else "", "starts": starts, "open": open_at is not None})
    return out


def budget(sd, evs, end):
    """(見積合計, ロック後の作業時間, 超過しているか)"""
    est = sum(x.get("est_min", 0) for x in rj(sd / "plan.json").get("steps", [])) if (sd / "plan.json").exists() else 0
    first_lock = next((ts(e["at"]) for e in evs if e["event"] == "lock"), None)
    used = active_min([ts(e["at"]) for e in evs], first_lock, end)[0] if first_lock else 0
    return est, used, bool(est) and used > est * OVERRUN


def scope_guard(sd):
    est, used, over = budget(sd, events(sd), ts(now()))
    parked = sum(e["event"] == "note" and e["type"] == "park" for e in events(sd))
    print(f"-- 見積 {est} 分 / ロック後の作業 {used} 分 / 見送った論点 {parked} 件")
    if over:
        print(f"!! 見積の {OVERRUN} 倍を超えました。先へ進む前に人に聞く: ①このまま続ける ②ここまでで閉じる ③残りを別タスクに割る")
        print("   答えを note --type decide で残す。範囲が広がっているなら、広がった分は note --type park へ。")


# ---------------------------------------------------------------- ODSC
def odsc():
    """ODSC.md の ODSC の節（目的に立ち返るために、手順の開始時と再開時に表示する）。"""
    f = pdir() / "ODSC.md"
    s = f.read_text(encoding="utf-8")
    i = s.find("## 1. ODSC")
    j = s.find("\n## ", i + 5) if i >= 0 else -1
    return s[i:j if j > 0 else len(s)].strip() if i >= 0 else s.strip()


def odsc_ids():
    return sorted(set(re.findall(SC_RE, (pdir() / "ODSC.md").read_text(encoding="utf-8"))))


def odsc_state():
    try:
        p = pdir()
    except HkError as e:
        return False, str(e), 0
    f, lf = p / "ODSC.md", p / "odsc.lock.json"
    if not lf.exists():
        return False, "ODSC が未ロック（人が決定してから: python harness/hk.py odsc lock）", 0
    h = rj(lf)["history"]
    if sha(f) != h[-1]["sha256"]:
        return False, "ODSC.md がロック後に書き換えられた（目的を変えるなら odsc lock --reason \"理由\"）", len(h) - 1
    return True, "ok", len(h) - 1


def cmd_odsc(a):
    p = pdir()
    f, lf = p / "ODSC.md", p / "odsc.lock.json"
    if a.action == "show":
        print(f.read_text(encoding="utf-8"))
        print("ロック: " + odsc_state()[1])
        return 0
    if "【未記入】" in f.read_text(encoding="utf-8"):
        raise HkError(f"ODSC.md に【未記入】が残っています（{f}）")
    if not odsc_ids():
        raise HkError("ODSC.md に Success Criteria（SC1, SC2 …）がありません")
    lock = rj(lf) if lf.exists() else {"history": []}
    if lock["history"] and not a.reason:
        raise HkError("ロック済みです。目的・成功の条件を変えるなら --reason \"変更理由\" を付けて再ロック（履歴に残ります）")
    lock["history"].append({"at": now(), "reason": a.reason or "初回ロック", "sha256": sha(f)})
    wj(lf, lock)
    write_status()
    print(f"ODSC をロックしました（{len(lock['history'])} 回目）。Success Criteria: {', '.join(odsc_ids())}")


# ---------------------------------------------------------------- 現在地・FB・共有
def open_tasks():
    td = pdir() / "tasks"
    rows = [rj(x) for x in sorted(td.glob("*/task.json"))]
    return [t for t in rows if t["status"] not in ("done", "abandoned")]


def lock_state(sd):
    lf = sd / "lock.json"
    if not lf.exists():
        return False, "未ロック（人が決定してから lock する）", 0
    lock = rj(lf)
    cur = lock["history"][-1]["files"]
    changed = [n for n in LOCKED if sha(sd / n) != cur[n]]
    if changed:
        return False, "ロック後に書き換えられた: " + ", ".join(changed), len(lock["history"]) - 1
    return True, "ok", len(lock["history"]) - 1


def resume_lines(sd):
    t = rj(sd / "task.json")
    evs = events(sd)
    L = [f"== {t['id']} [{t['status']}] {t['title']}（{t.get('kind', 'calc')}）", f"指示書: {sd}", f"ロック: {lock_state(sd)[1]}"]
    for st in step_times(sd, evs, ts(now())):
        state = "作業中" if st["open"] else ("済" if st["starts"] else "未着手")
        L.append(f"  {st['id']} {st['name']}: {state}（見積 {st['est_min']} 分 / 実績 {st['actual_min'] or 0} 分 / 着手 {st['starts']} 回）")
    ev = rj(sd / "eval.json")["attempts"] if (sd / "eval.json").exists() else []
    if ev:
        L.append(f"直近の Eval（{len(ev)} 回目）: {'PASS' if ev[-1]['pass'] else 'FAIL'}")
        L += [f"  FAIL {r['id']}: {r['detail']}" for r in ev[-1]["results"] if not r["pass"]]
    if (sd / "log.md").exists():
        L += ["直近の経過:"] + ["  " + l for l in (sd / "log.md").read_text(encoding="utf-8").splitlines()[-10:]]
    return L


def fb_new():
    """上長の FB に、まだ対応を記録していない書き込みがあるか。"""
    p = pdir()
    f, ack = p / "FEEDBACK.md", p / "feedback_ack.json"
    return f.exists() and (not ack.exists() or rj(ack)["sha256"] != sha(f))


def cmd_fb(a):
    p = pdir()
    f = p / "FEEDBACK.md"
    if a.action == "show":
        print(f.read_text(encoding="utf-8") if f.exists() else "FB はまだありません")
        print("\n" + ("※ まだ対応を記録していない FB があります。対応したら: python harness/hk.py fb ack \"対応した内容\"" if fb_new() else "未対応の FB はありません"))
        return 0
    if not a.text:
        raise HkError("fb ack \"対応した内容\" が必要です（何をどう直したか・直さないならその理由）")
    with (p / "FEEDBACK_RESPONSES.md").open("a", encoding="utf-8") as g:
        if g.tell() == 0:
            g.write(f"# {ROOT.name} — FB への対応（追記のみ。ハーネスが書く）\n\n")
        g.write(f"- {now()[:16]} {a.text}\n")
    wj(p / "feedback_ack.json", {"sha256": sha(f), "at": now()})
    write_status()
    print("FB への対応を記録しました")
    return 0


def status_text():
    p = pdir()
    L = [f"# 現在地 — {ROOT.name}", "", f"更新: {now()}", f"担当: {member()['name']}", f"ODSC: {odsc_state()[1]}",
         f"FB: {'未対応あり' if fb_new() else '未対応なし'}", ""]
    if (p / "NEXT_SESSION.md").exists():
        L += ["## 次にやること", "", (p / "NEXT_SESSION.md").read_text(encoding="utf-8").strip(), ""]
    ot = open_tasks()
    L += [f"## 未完了タスク {len(ot)} 件", ""]
    for t in ot:
        L += ["```"] + resume_lines(p / "tasks" / t["id"]) + ["```", ""]
    return "\n".join(L) + "\n"


def write_status():
    """現在地（STATUS.md）を書き直す。コマンドのたびに呼ぶ。失敗しても作業は止めない。"""
    try:
        (pdir() / "STATUS.md").write_text(status_text(), encoding="utf-8")
    except Exception as e:
        print(f"（現在地の更新に失敗: {e}）")


def vault_pull():
    v = vault()
    if not v or git("rev-parse", "--is-inside-work-tree", cwd=v).returncode != 0:
        return None
    r = git("pull", "--rebase", "--autostash", cwd=v, timeout=45)
    return "最新" if r.returncode == 0 else "取り込めませんでした（" + (r.stdout + r.stderr).strip().splitlines()[-1:][0][:120] + "）" if (r.stdout + r.stderr).strip() else "取り込めませんでした"


def publish(msg):
    """vault（指示書）とこのリポジトリ（コード）を GitHub へ送る。失敗しても手元には残る。"""
    write_status()
    for cwd, paths, label in ((vault(), [f"projects/{ROOT.name}"], "vault（指示書）"), (ROOT, ["tasks", "harness/member.json"], "リポジトリ（コード）")):
        if git("rev-parse", "--is-inside-work-tree", cwd=cwd).returncode != 0:
            print(f"共有 {label}: git 未設定のため送信なし"); continue
        git("add", "--", *[x for x in paths if (Path(cwd) / x).exists()], cwd=cwd)
        if git("diff", "--cached", "--quiet", cwd=cwd).returncode == 0:
            print(f"共有 {label}: 変更なし"); continue
        c = git("commit", "-m", f"[{ROOT.name}] {msg}", cwd=cwd)
        if c.returncode != 0:
            print(f"共有 {label}: コミットできず → " + (c.stdout + c.stderr).strip()[-200:]); continue
        for step in (("pull", "--rebase", "--autostash"), ("push",)):
            r = git(*step, cwd=cwd)
            if r.returncode != 0:
                print(f"共有 {label}: git {step[0]} に失敗（コミット済み。上長に伝える）→ " + (r.stdout + r.stderr).strip()[-200:]); break
        else:
            print(f"共有 {label}: GitHub に送信しました")


# ---------------------------------------------------------------- init / new / lock
def cmd_init(a):
    if not re.fullmatch(r"[A-Z]{2,4}", a.code):
        raise HkError("--code は英大文字 2〜4 字（例: MZ）")
    if not vault():
        raise HkError(VAULT_MSG)
    f = ROOT / "harness" / "member.json"
    rid = rj(f).get("repo_id") if f.exists() else None
    wj(f, {"name": a.name, "code": a.code, "repo_id": rid or uuid.uuid4().hex[:12]})
    p = pdir(create=True)
    print(f"登録しました: {ROOT.name} / 担当 {a.name} ({a.code})")
    print(f"指示書フォルダ: {p}")
    print("次: ODSC.md を人と決めてから python harness/hk.py odsc lock")


def cmd_new(a):
    ok, msg, _ = odsc_state()
    if not ok:
        raise HkError("タスクを作る前に ODSC を決めてください。" + msg)
    m = member()
    td = pdir() / "tasks"
    nums = [int(x.name.split("-")[1]) for x in td.glob(f"{m['code']}-[0-9][0-9][0-9]")]
    tid = f"{m['code']}-{(max(nums) + 1 if nums else 1):03d}"
    sd = td / tid
    sd.mkdir(parents=True)
    rep = {"{{ID}}": tid, "{{TITLE}}": a.title, "{{MEMBER}}": m["name"], "{{DATE}}": now()[:10]}
    for src, name in ((f"spec_{a.kind}.md", "spec.md"), ("checks.json", "checks.json"), ("plan.json", "plan.json")):
        text = (TEMPLATES / src).read_text(encoding="utf-8")
        for k, v in rep.items():
            text = text.replace(k, v)
        (sd / name).write_text(text, encoding="utf-8")
    wj(sd / "task.json", {"id": tid, "kind": a.kind, "title": a.title, "member": m["name"], "created": now(), "status": "draft"})
    cdir(tid)
    log_event(sd, "new")
    write_status()
    print(f"作成: {tid}")
    print(f"  指示書（vault）: {sd}  → spec.md・checks.json・plan.json を埋めてから lock")
    print(f"  コード（ここ）: tasks/{tid}/  （src/ data/ out/）")


def cmd_lock(a):
    sd = sdir(a.task)
    ok, msg, _ = odsc_state()
    if not ok:
        raise HkError(msg)
    spec = (sd / "spec.md").read_text(encoding="utf-8")
    if "【未記入】" in spec:
        raise HkError("spec.md に【未記入】が残っています")
    refs = set(re.findall(SC_RE, spec))
    if not refs or not refs <= set(odsc_ids()):
        raise HkError(f"spec.md に、このタスクが対応する Success Criteria を書いてください（ODSC.md にあるもの: {', '.join(odsc_ids())}）。"
                      "どれにも当たらないなら、このプロジェクトの目的の外です")
    checks = rj(sd / "checks.json").get("checks", [])
    if not checks:
        raise HkError("checks.json に検査が 1 つもありません")
    for c in checks:
        if c.get("type") not in CHECKS:
            raise HkError(f"未知の検査 type: {c.get('type')}（使えるもの: {', '.join(CHECKS)}）")
        if not c.get("id") or not c.get("why"):
            raise HkError("すべての検査に id と why（なぜこの検査か）が必要です")
    steps = rj(sd / "plan.json").get("steps", [])
    if not steps:
        raise HkError("plan.json に手順が 1 つもありません")
    for st in steps:
        if st.get("type") not in STEP_TYPES or not st.get("id") or not st.get("name") or not isinstance(st.get("est_min"), (int, float)):
            raise HkError(f"plan.json の手順には id・name・type（{' / '.join(STEP_TYPES)}）・est_min（分）が必要です: {st}")
    lf = sd / "lock.json"
    lock = rj(lf) if lf.exists() else {"history": []}
    if lock["history"] and not a.reason:
        raise HkError("ロック済みです。仕様を変えるなら --reason \"変更理由\" を付けて再ロック（履歴に残ります）")
    lock["history"].append({"at": now(), "reason": a.reason or "初回ロック", "files": {n: sha(sd / n) for n in LOCKED}})
    wj(lf, lock)
    t = rj(sd / "task.json"); t["status"] = "locked"; wj(sd / "task.json", t)
    log_event(sd, "lock", reason=a.reason or "初回ロック")
    write_status()
    print(f"ロックしました（{len(lock['history'])} 回目）: {a.task}")


# ---------------------------------------------------------------- fetch / source
def cmd_fetch(a):
    sd, cd = sdir(a.task), cdir(a.task)
    if not re.fullmatch(r"[A-Za-z0-9_]+", a.name):
        raise HkError("名前は英数字と _ のみ")
    sql = Path(a.sql_file).read_text(encoding="utf-8") if a.sql_file else a.sql
    if not sql:
        raise HkError("--sql か --sql-file が必要です")
    out = cd / "data" / f"{a.name}.csv"
    cols, rows, key = query_to_csv(sql, a.source, out)
    (cd / "data" / f"{a.name}.sql").write_text(sql.strip() + "\n", encoding="utf-8")
    mf = cd / "data" / "_manifest.json"
    man = rj(mf) if mf.exists() else {}
    man[a.name] = {"fetched_at": now(), "source": key, "rows": len(rows), "columns": cols,
                   "sha256": sha(out), "sha256_sorted": sorted_sha(out)}
    wj(mf, man)
    log_event(sd, "fetch", name=a.name, rows=len(rows))
    print(f"取得: tasks/{a.task}/data/{a.name}.csv  {len(rows)} 行 / {len(cols)} 列")


def cmd_source(a):
    """原本（Excel・CSV など）を登録する。中身は写さず、場所とハッシュだけ控える。"""
    sd, cd = sdir(a.task), cdir(a.task)
    p = Path(a.path)
    p = p if p.is_absolute() else ROOT / p
    if not p.is_file():
        raise HkError(f"ファイルがありません: {a.path}")
    mf = cd / "data" / "_manifest.json"
    man = rj(mf) if mf.exists() else {}
    try:
        where = p.resolve().relative_to(ROOT.resolve()).as_posix()  # リポジトリ内なら相対（他の PC でも辿れる）
    except ValueError:
        where = str(p)
    man[f"file:{p.name}"] = {"fetched_at": now(), "source": where, "rows": "-", "sha256": sha(p)}
    wj(mf, man)
    log_event(sd, "source", name=p.name)
    print(f"原本を登録: {p.name}  sha256 {man['file:' + p.name]['sha256'][:12]}")


# ---------------------------------------------------------------- 検査
def read_csv(p):
    with Path(p).open(encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def num(v):
    try:
        return Decimal(str(v).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        raise HkError(f"数値として読めない値: {v!r}")


def blank(v):
    return v is None or str(v).strip() == ""


def col(rows, c):
    if rows and c not in rows[0]:
        raise HkError(f"列 {c} がありません（ある列: {', '.join(rows[0].keys())}）")
    return [r[c] for r in rows]


def some(xs, n=5):
    xs = list(xs)
    return ", ".join(str(x) for x in xs[:n]) + (f" …他 {len(xs) - n} 件" if len(xs) > n else "")


def ck_row_count(d, c):
    n = len(read_csv(safe(d, c["file"])))
    ok = all([("equals" not in c or n == c["equals"]), ("min" not in c or n >= c["min"]), ("max" not in c or n <= c["max"])])
    return ok, f"{n} 行"


def ck_not_null(d, c):
    rows = read_csv(safe(d, c["file"]))
    bad = {k: [i + 2 for i, v in enumerate(col(rows, k)) if blank(v)] for k in c["columns"]}
    bad = {k: v for k, v in bad.items() if v}
    return not bad, "空欄なし" if not bad else "; ".join(f"{k}: {len(v)} 件（行 {some(v)}）" for k, v in bad.items())


def ck_unique(d, c):
    rows = read_csv(safe(d, c["file"]))
    seen, dup = set(), []
    for k in zip(*[col(rows, x) for x in c["columns"]]):
        (dup if k in seen else seen).append(k) if k in seen else seen.add(k)
    return not dup, "重複なし" if not dup else f"重複 {len(dup)} 件: {some(['|'.join(k) for k in dup])}"


def ck_allowed_values(d, c):
    rows = read_csv(safe(d, c["file"]))
    bad = sorted({v for v in col(rows, c["column"]) if v not in c["values"]})
    return not bad, "すべて許容値" if not bad else f"許容外: {some(bad)}"


def ck_range(d, c):
    rows = read_csv(safe(d, c["file"]))
    bad = []
    for i, v in enumerate(col(rows, c["column"])):
        if blank(v):
            continue
        x = num(v)
        if ("min" in c and x < num(c["min"])) or ("max" in c and x > num(c["max"])):
            bad.append(f"行{i + 2}={v}")
    return not bad, "範囲内" if not bad else f"範囲外 {len(bad)} 件: {some(bad)}"


def total(d, f, c):
    return sum((num(v) for v in col(read_csv(safe(d, f)), c) if not blank(v)), Decimal(0))


def ck_sum_equals(d, c):
    got = total(d, c["file"], c["column"])
    exp = num(c["expected"]) if "expected" in c else total(d, c["other_file"], c["other_column"])
    tol = num(c.get("tolerance", 0))
    return abs(got - exp) <= tol, f"合計 {got} / 期待 {exp} / 差 {got - exp}（許容 {tol}）"


def group(d, f, key, c):
    out = {}
    for r in read_csv(safe(d, f)):
        if key not in r or c not in r:
            raise HkError(f"{f} に列 {key} / {c} がありません")
        out[r[key]] = out.get(r[key], Decimal(0)) + (Decimal(0) if blank(r[c]) else num(r[c]))
    return out


def ck_reconcile(d, c):
    a = group(d, c["file_a"], c["key"], c["column_a"])
    b = group(d, c["file_b"], c.get("key_b", c["key"]), c["column_b"])
    tol = num(c.get("tolerance", 0))
    bad = [f"{k}: {a.get(k, '無')} vs {b.get(k, '無')}" for k in sorted(set(a) | set(b))
           if k not in a or k not in b or abs(a[k] - b[k]) > tol]
    return not bad, f"{len(a)} キー一致" if not bad else f"不一致 {len(bad)} キー: {some(bad)}"


def ck_foreign_key(d, c):
    ref = set(col(read_csv(safe(d, c["ref_file"])), c["ref_column"]))
    bad = sorted({v for v in col(read_csv(safe(d, c["file"])), c["column"]) if not blank(v) and v not in ref})
    return not bad, "参照先にすべて存在" if not bad else f"参照先に無い値 {len(bad)} 種: {some(bad)}"


def ck_file_exists(d, c):
    p = safe(d, c["path"])
    return p.exists() and p.stat().st_size > 0, f"{c['path']} " + ("あり" if p.exists() else "なし")


def ck_sources_unchanged(d, c):
    mf = d / "data" / "_manifest.json"
    files = {k: v for k, v in (rj(mf) if mf.exists() else {}).items() if k.startswith("file:")}
    if not files:
        return False, "登録された原本がありません（hk source で登録）"
    loc = lambda v: Path(v["source"]) if Path(v["source"]).is_absolute() else ROOT / v["source"]
    bad = [k[5:] for k, v in files.items() if not loc(v).is_file() or sha(loc(v)) != v["sha256"]]
    return not bad, f"原本 {len(files)} 件とも登録時のまま" if not bad else f"原本が変わった／無い: {some(bad)}"


def ck_command(d, c):
    r = subprocess.run(c["cmd"], shell=True, cwd=d, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=c.get("timeout", 300))
    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
    return r.returncode == 0, f"終了コード {r.returncode}" + (": " + " / ".join(tail) if r.returncode else "")


CHECKS = {"row_count": ck_row_count, "not_null": ck_not_null, "unique": ck_unique,
          "allowed_values": ck_allowed_values, "range": ck_range, "sum_equals": ck_sum_equals,
          "reconcile": ck_reconcile, "foreign_key": ck_foreign_key, "file_exists": ck_file_exists,
          "sources_unchanged": ck_sources_unchanged,
          "command": ck_command}

def run_checks(tid):
    sd, cd = sdir(tid), cdir(tid)
    lock_ok, lock_msg, _ = lock_state(sd)
    o_ok, o_msg, _ = odsc_state()
    if not o_ok:
        lock_ok, lock_msg = False, o_msg
    results = []
    for c in rj(sd / "checks.json").get("checks", []):
        try:
            ok, detail = CHECKS[c["type"]](cd, c)
        except Exception as e:  # 検査が実行できない = 不合格
            ok, detail = False, f"実行不可: {e}"
        results.append({"id": c.get("id"), "type": c.get("type"), "pass": bool(ok), "detail": detail})
    return lock_ok, lock_msg, results, lock_ok and bool(results) and all(r["pass"] for r in results)


def cmd_eval(a):
    sd = sdir(a.task)
    lock_ok, lock_msg, results, passed = run_checks(a.task)
    ef = sd / "eval.json"
    ev = rj(ef) if ef.exists() else {"task": a.task, "attempts": []}
    ev["attempts"].append({"at": now(), "pass": passed, "lock_ok": lock_ok, "lock_msg": lock_msg, "results": results})
    wj(ef, ev)
    log_event(sd, "eval", **{"pass": passed, "failed": [r["id"] for r in results if not r["pass"]]})
    write_status()
    print(f"== Eval {a.task}  {len(ev['attempts'])} 回目 ==")
    print(f"[{'PASS' if lock_ok else 'FAIL'}] ロック: {lock_msg}")
    for r in results:
        print(f"[{'PASS' if r['pass'] else 'FAIL'}] {r['id']} ({r['type']}): {r['detail']}")
    print("結果: " + ("PASS" if passed else "FAIL"))
    return 0 if passed else 1


# ---------------------------------------------------------------- step / note / resume / save
def cmd_step(a):
    sd = sdir(a.task)
    ids = [x["id"] for x in rj(sd / "plan.json").get("steps", [])]
    if a.step not in ids:
        raise HkError(f"手順 {a.step} は plan.json にありません（{', '.join(ids)}）")
    log_event(sd, "step", step=a.step, state=a.state)
    print(f"{a.task} {a.step}: {'開始' if a.state == 'start' else '完了'}")
    if a.state == "start":
        print(odsc())
    scope_guard(sd)
    write_status()


def cmd_note(a):
    sd = sdir(a.task)
    with (sd / "log.md").open("a", encoding="utf-8") as f:
        f.write(f"- {now()[:16]} [{a.type}] {a.text}\n")
    log_event(sd, "note", type=a.type, text=a.text)
    write_status()
    print("記録しました")


def cmd_resume(a):
    """/clear の後や別の日に、ここから再開する。ID を省くと未完了のタスクすべて。"""
    p = pdir()
    print(f"指示書フォルダ: {p}")
    print(f"ODSC ロック: {odsc_state()[1]}")
    print(odsc())
    if fb_new():
        print("\n== 上長の FB（まだ対応を記録していない書き込みがあります。最初に読む）\n" + (p / "FEEDBACK.md").read_text(encoding="utf-8").strip()[-3000:])
    if (p / "NEXT_SESSION.md").exists():
        print("\n== 次にやること（前回の終わりに残したもの）\n" + (p / "NEXT_SESSION.md").read_text(encoding="utf-8").strip())
    ids = [a.task] if a.task else [t["id"] for t in open_tasks()]
    for tid in ids:
        sd = sdir(tid)
        print("\n" + "\n".join(resume_lines(sd)))
        scope_guard(sd)
    if not ids:
        print("\n未完了のタスクはありません")
    return 0


def cmd_save(a):
    """会話を終える前・/clear の前に呼ぶ。次の一手を残し（上書き）、vault とリポジトリを送る。"""
    (pdir() / "NEXT_SESSION.md").write_text(f"{now()[:16]}\n\n{a.text}\n", encoding="utf-8")
    publish(f"save: {a.text[:60]}")
    print("保存しました。再開は: python harness/hk.py resume")
    return 0


def cmd_rules(a):
    v = vault()
    if not v:
        raise HkError(VAULT_MSG)
    f = next((x for x in (v / "AI" / "Decisions" / "RULES.md", v / "RULES.md") if x.exists()), None)
    print(f.read_text(encoding="utf-8") if f else "裁定（AI/Decisions/RULES.md）はまだありません")
    return 0


def cmd_update(a):
    """テンプレート（既定: ../team-harness-kit）からハーネス本体を取り込む。タスクと vault の指示書には触れない。"""
    src = Path(a.source) if a.source else ROOT.parent / "team-harness-kit"
    if not (src / "VERSION").exists():
        raise HkError(f"テンプレートが見つかりません: {src}（--from で場所を指定）")
    if src.resolve() == ROOT.resolve():
        raise HkError("ここがテンプレート自身です")
    old = version()
    for top in ("harness", ".claude", "formats", "docs"):
        for f in (src / top).rglob("*"):
            if f.is_file() and "__pycache__" not in f.parts and f.name != "member.json":
                t = ROOT / f.relative_to(src)
                t.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, t)
    for name in ("CLAUDE.md", "README.md", "VERSION", ".gitignore", ".env.example"):
        if (src / name).exists():
            shutil.copy2(src / name, ROOT / name)
    print(f"更新しました: {old} → {version()}")
    return 0


# ---------------------------------------------------------------- close
def cmd_close(a):
    sd, cd, p = sdir(a.task), cdir(a.task), pdir()
    t = rj(sd / "task.json")
    ev = rj(sd / "eval.json")["attempts"] if (sd / "eval.json").exists() else []
    if not a.abandon and not (ev and ev[-1]["pass"]):
        raise HkError("最後の Eval が PASS ではありません。eval を通すか、--abandon \"理由\" で中止として閉じる")
    if not a.abandon and not (cd / "src" / "run.py").exists():
        raise HkError("src/run.py がありません。data/ から out/ を作り直す入口を 1 本置いてください（上長が再現して確かめるため）")
    if not a.abandon and not a.learning:
        raise HkError("--learning \"今回の学び\" が必要です（次に同じ作業をする人への 1〜2 文）")
    _, _, relocks = lock_state(sd)
    rv = sd / "review.md"
    review = "none"
    if rv.exists():
        head = rv.read_text(encoding="utf-8")[:200].upper()
        review = "pass" if "PASS" in head else ("fail" if "FAIL" in head else "written")
    status = "abandoned" if a.abandon else "done"
    log_event(sd, "close", status=status)
    evs = events(sd)
    closed = evs[-1]["at"]
    active, sessions = active_min([ts(e["at"]) for e in evs])
    steps = step_times(sd, evs, ts(closed))
    first_lock = next((ts(e["at"]) for e in evs if e["event"] == "lock"), None)
    design_min = active_min([ts(e["at"]) for e in evs], None, first_lock)[0] if first_lock else ""
    notes = [e for e in evs if e["event"] == "note"]
    fixes = sum(e["type"] == "fix" for e in notes)
    parked = [e["text"] for e in notes if e["type"] == "park"]
    est_total, used_after_lock, _ = budget(sd, evs, ts(closed))
    design = next((e["text"] for e in reversed(notes) if e["type"] == "design"), "-")
    minutes = int((ts(closed) - ts(t["created"])).total_seconds() // 60)
    man = rj(cd / "data" / "_manifest.json") if (cd / "data" / "_manifest.json").exists() else {}
    checks_n = len(rj(sd / "checks.json").get("checks", []))
    first_pass = bool(ev and ev[0]["pass"])
    name = t["member"]

    L = ["---", "type: task-record", f"project: {ROOT.name}", f"task: {t['id']}", f"kind: {t.get('kind', 'calc')}", f"title: \"{t['title']}\"", f"member: {name}",
         f"status: {status}", f"created: {t['created']}", f"closed: {closed}", f"minutes: {minutes}",
         f"active_minutes: {active}", f"design_minutes: {design_min}", f"sessions: {sessions}", f"fixes: {fixes}",
         f"est_minutes: {est_total}", f"after_lock_minutes: {used_after_lock}", f"parked: {len(parked)}", f"design: {design}",
         f"eval_attempts: {len(ev)}", f"first_pass: {str(first_pass).lower()}", f"checks: {checks_n}",
         f"relocks: {relocks}", f"review: {review}", "---", "", f"# {t['id']} — {t['title']}", "",
         f"指示書: [[projects/{ROOT.name}/tasks/{t['id']}/spec]]　ODSC: [[projects/{ROOT.name}/ODSC]]", ""]
    if a.abandon:
        L += ["## 中止理由", "", a.abandon, ""]
    if parked:
        L += ["## 見送った論点（今回の目的の外。次のタスク・別プロジェクトの候補）", ""] + [f"- {x}" for x in parked] + [""]
    L += ["## 学び", ""] + [f"- {x}" for x in (a.learning or [])] + [""]
    L += ["## Eval 経過", "", "| 回 | 時刻 | 結果 | 不合格の検査 |", "|---|---|---|---|"]
    for i, e in enumerate(ev, 1):
        fails = [r["id"] for r in e["results"] if not r["pass"]] + ([] if e["lock_ok"] else ["ロック"])
        L.append(f"| {i} | {e['at']} | {'PASS' if e['pass'] else 'FAIL'} | {', '.join(fails) or '-'} |")
    if ev:
        L += ["", "### 最終回の検査結果", ""] + [f"- [{'PASS' if r['pass'] else 'FAIL'}] {r['id']}: {r['detail']}" for r in ev[-1]["results"]]
    L += ["", "## 分解と所要時間", "", f"作業時間 {active} 分（会話 {sessions} 回、経過 {minutes} 分。{IDLE_CAP_MIN} 分を超える空きは数えない）", "",
          "| 手順 | 種類 | 内容 | 見積(分) | 実績(分) | 着手回数 |", "|---|---|---|---|---|---|"]
    L += [f"| {x['id']} | {x['type']} | {x['name']} | {x['est_min']} | {x['actual_min']} | {x['starts']} |" for x in steps]
    if (sd / "log.md").exists():
        L += ["", "## 経過（試行・修正・判断）", "", (sd / "log.md").read_text(encoding="utf-8").strip()]
    L += ["", "## 取得データ", ""] + ([f"- {k}: {v['rows']} 行, {v['source']}, sha256 {v['sha256'][:12]}, {v['fetched_at']}" for k, v in man.items()] or ["- なし"])
    L += ["", "## 仕様ロックの履歴", ""] + ([f"- {h['at']}: {h['reason']}" for h in rj(sd / "lock.json")["history"]] if (sd / "lock.json").exists() else ["- なし"])
    if rv.exists():
        L += ["", "## 独立レビュー", "", rv.read_text(encoding="utf-8").strip()]
    L += [""]
    (p / "records").mkdir(exist_ok=True)
    (p / "records" / f"{t['id']}.md").write_text("\n".join(L), encoding="utf-8")

    with (p / "LEARNINGS.md").open("a", encoding="utf-8") as f:
        for x in (a.learning or []):
            f.write(f"- {closed[:10]} [{t['id']}] {x}\n")
        if a.abandon:
            f.write(f"- {closed[:10]} [{t['id']}] 中止: {a.abandon}\n")
    lg = p / "_LEDGER.csv"
    new = not lg.exists()
    with lg.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["task", "kind", "title", "created", "closed", "status", "minutes", "eval_attempts", "first_pass", "checks", "relocks", "review", "active_minutes", "sessions", "fixes", "design", "design_minutes", "est_minutes", "after_lock_minutes", "parked"])
        w.writerow([t["id"], t.get("kind", "calc"), t["title"], t["created"], closed, status, minutes, len(ev), first_pass, checks_n, relocks, review, active, sessions, fixes, design, design_min, est_total, used_after_lock, len(parked)])
    sf = p / "_STEPS.csv"
    new = not sf.exists()
    with sf.open("a", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["task", "kind", "step", "type", "name", "est_min", "actual_min", "starts"])
        for x in steps:
            w.writerow([t["id"], t.get("kind", "calc"), x["id"], x["type"], x["name"], x["est_min"], x["actual_min"], x["starts"]])
    t.update(status=status, closed=closed); wj(sd / "task.json", t)
    print(f"記録: vault の projects/{ROOT.name}/records/{t['id']}.md")
    if a.no_push:
        write_status()
        print("共有: --no-push のため送信なし")
    else:
        publish(f"close {t['id']}: {t['title']} [{status}]")
    return 0


# ---------------------------------------------------------------- audit（上長側）
def cmd_audit(a):
    """共有されたタスクを取り直し・作り直し・検査し直す。タスクの履歴（eval.json・events）は書き換えない。"""
    sd, cd = sdir(a.task), cdir(a.task)
    t = rj(sd / "task.json")
    out = [f"# 監査 {ROOT.name} / {t['id']} — {t['title']}", "", f"- 担当: {t['member']} / 種類: {t.get('kind', 'calc')} / 状態: {t['status']}", f"- 実行: {now()}"]
    ev = rj(sd / "eval.json")["attempts"] if (sd / "eval.json").exists() else []
    out.append(f"- 担当者の最終 Eval: {('PASS' if ev[-1]['pass'] else 'FAIL') if ev else 'なし'}（{len(ev)} 回）")
    man = rj(cd / "data" / "_manifest.json") if (cd / "data" / "_manifest.json").exists() else {}
    out += ["", "## 1. データの取り直し", ""]
    unknown = False
    for name, m in man.items():
        if name.startswith("file:"):
            fp = Path(m["source"]) if Path(m["source"]).is_absolute() else ROOT / m["source"]
            if not fp.is_file():
                out.append(f"- 原本 {name[5:]}: 確かめられない（この PC に無い: {m['source']}）"); unknown = True
            else:
                out.append(f"- 原本 {name[5:]}: " + ("登録時と同じ" if sha(fp) == m["sha256"] else "**登録時から変わっている**"))
            continue
        try:
            src = "default" if m["source"] == "DATABASE_URL" else m["source"][3:].lower()
            csvp = cd / "data" / f"{name}.csv"
            _, rows, _ = query_to_csv((cd / "data" / f"{name}.sql").read_text(encoding="utf-8"), src, csvp)
            if "sha256_sorted" in m and sorted_sha(csvp) == m["sha256_sorted"]:
                out.append(f"- {name}: 担当者の取得と同じ（{len(rows)} 行）")
            else:
                out.append(f"- {name}: **担当者の取得と違う**（当時 {m['rows']} 行 → 今 {len(rows)} 行。元データが動いたか、取得条件に日付の固定が無い）")
        except Exception as e:
            out.append(f"- {name}: 取り直せない（{e}）"); unknown = True
    if not man:
        out.append("- 取得の記録なし")
    out += ["", "## 2. 成果物の作り直し", ""]
    if (cd / "src" / "run.py").exists():
        r = subprocess.run([sys.executable, "src/run.py"], cwd=cd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
        out.append(f"- src/run.py: 終了コード {r.returncode}" + ("" if r.returncode == 0 else " — " + " / ".join((r.stdout + r.stderr).strip().splitlines()[-3:])))
        unknown = unknown or r.returncode != 0
    else:
        out.append("- src/run.py が無い（作り直せない）"); unknown = True
    lock_ok, lock_msg, results, passed = run_checks(a.task)
    out += ["", "## 3. 検査のやり直し", "", f"- [{'PASS' if lock_ok else 'FAIL'}] ロック: {lock_msg}"]
    out += [f"- [{'PASS' if r['pass'] else 'FAIL'}] {r['id']} ({r['type']}): {r['detail']}" for r in results]
    verdict = "再現できた（PASS）" if passed and not unknown else ("再現したが不合格（FAIL）" if not unknown else "判定できない（上の「確かめられない／取り直せない」を解消する）")
    out += ["", f"## 結果: {verdict}", "", "## 所見（AI・人が書く）", "", "- "]
    text = "\n".join(out) + "\n"
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"書き出し: {a.out}")
    print(text)
    return 0 if passed and not unknown else 1


# ---------------------------------------------------------------- status（会話の最初に自動表示）
def cmd_status(a):
    try:
        who = member()["name"]
        if not vault():
            print(f"[harness] {ROOT.name} / 担当: {who}\n  ! {VAULT_MSG}"); return 0
        pulled = vault_pull()
        p = pdir()
        ot = open_tasks()
        print(f"[harness] {ROOT.name} / 担当: {who} / ODSC: {odsc_state()[1]} / 未完了タスク {len(ot)} 件" + (f" / vault: {pulled}" if pulled else ""))
        print(f"  指示書（正本）: {p}")
        tv = ROOT.parent / "team-harness-kit" / "VERSION"
        if tv.exists() and tv.parent.resolve() != ROOT.resolve() and tv.read_text(encoding="utf-8").strip() != version():
            print(f"  ! キットの新しい版があります（{tv.read_text(encoding='utf-8').strip()}）: python harness/hk.py update")
        if fb_new():
            print("  ! 上長の FB に未対応の書き込みがあります。最初に: python harness/hk.py fb show")
        if (p / "NEXT_SESSION.md").exists():
            print("  次にやること: " + " ".join((p / "NEXT_SESSION.md").read_text(encoding="utf-8").split()[1:])[:200])
        for t in ot:
            sd = p / "tasks" / t["id"]
            cur = [x["id"] for x in step_times(sd, events(sd), ts(now())) if x["open"]]
            print(f"  - {t['id']} [{t['status']}] {t['title']}" + (f" 作業中の手順: {', '.join(cur)}" if cur else ""))
        print("  続きは: python harness/hk.py resume")
    except HkError as e:
        print(f"[harness] {e}")
    return 0


# ---------------------------------------------------------------- stats（vault の全プロジェクトを横断）
def cmd_stats(a):
    v = vault()
    if not v:
        raise HkError(VAULT_MSG)
    rows, steps = [], []
    owner = lambda f: rj(f.parent / "REPO.json").get("member", "?") if (f.parent / "REPO.json").exists() else "?"
    for f in sorted((v / "projects").glob("*/_LEDGER.csv")):
        rows += [{**r, "member": owner(f), "project": f.parent.name} for r in read_csv(f)]
    for f in sorted((v / "projects").glob("*/_STEPS.csv")):
        steps += [{**r, "member": owner(f)} for r in read_csv(f)]
    if not rows:
        print("実績がまだありません"); return 0
    f_ = lambda x: float(x) if str(x).strip() not in ("", "-") else None
    med = lambda xs: round(statistics.median(xs), 1) if xs else "-"
    avg = lambda xs: round(statistics.mean(xs), 2) if xs else "-"
    out = ["# 実績の集計", "", f"タスク {len(rows)} 件 / プロジェクト {len({r['project'] for r in rows})} 件（{IDLE_CAP_MIN} 分を超える空きは作業時間に数えない）", "",
           "## 担当 × 種類", "", "| 担当 | 種類 | 設計 | 件数 | 作業時間 中央値(分) | うち計画・設計 中央値(分) | 会話 平均 | Eval 平均 | 初回通過 | 修正 平均 | 再ロック 平均 | 実績÷見積 | 見送り 平均 | 中止 |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k in sorted({(r["member"], r["kind"], r.get("design", "-")) for r in rows}):
        g = [r for r in rows if (r["member"], r["kind"], r.get("design", "-")) == k]
        n = lambda c: [x for x in (f_(r.get(c, "")) for r in g) if x is not None]
        fp = sum(r["first_pass"] == "True" for r in g)
        ratio = round(sum(n('after_lock_minutes')) / sum(n('est_minutes')), 2) if sum(n('est_minutes')) else '-'
        out.append(f"| {k[0]} | {k[1]} | {k[2]} | {len(g)} | {med(n('active_minutes'))} | {med(n('design_minutes'))} | {avg(n('sessions'))} | {avg(n('eval_attempts'))} | {fp}/{len(g)} | {avg(n('fixes'))} | {avg(n('relocks'))} | {ratio} | {avg(n('parked'))} | {sum(r['status'] == 'abandoned' for r in g)} |")
    out += ["", "## 手順の種類ごと（見積と実績）", "", "| 種類 | 手順の種類 | 件数 | 見積 中央値(分) | 実績 中央値(分) | 実績÷見積 | やり直し（着手 2 回以上） |", "|---|---|---|---|---|---|---|"]
    for k in sorted({(r["kind"], r["type"]) for r in steps}):
        g = [r for r in steps if (r["kind"], r["type"]) == k and f_(r["actual_min"]) is not None]
        if not g:
            continue
        est, act = [f_(r["est_min"]) for r in g], [f_(r["actual_min"]) for r in g]
        out.append(f"| {k[0]} | {k[1]} | {len(g)} | {med(est)} | {med(act)} | {round(sum(act) / sum(est), 2) if sum(est) else '-'} | {sum(int(r['starts']) >= 2 for r in g)} |")
    text = "\n".join(out) + "\n"
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
        print(f"書き出し: {a.out}")
    else:
        print(text)
    return 0


# ---------------------------------------------------------------- selftest
def cmd_selftest(a):
    global ROOT, VAULT_OVERRIDE
    keep, keep_v = ROOT, VAULT_OVERRIDE
    base = Path(tempfile.mkdtemp(prefix="hk_selftest_"))
    try:
        ROOT = base / "odsc-selftest"
        VAULT_OVERRIDE = base / "team-vault"
        (ROOT / "harness").mkdir(parents=True)
        N = argparse.Namespace
        try:
            cmd_init(N(name="テスト", code="ST")); raise AssertionError("vault が無いのに登録できた")
        except HkError:
            pass
        VAULT_OVERRIDE.mkdir()
        con = sqlite3.connect(ROOT / "demo.db")
        con.executescript("""create table items(item_code text, name text, unit_price real);
            insert into items values('A-001','ギア',120.5),('A-002','スプリング',80),('A-003','シム',15.25);
            create table sales(item_code text, qty integer, amount real);
            insert into sales values('A-001',10,1205),('A-002',5,400),('A-001',2,241);""")
        con.commit(); con.close()
        (ROOT / ".env").write_text("DATABASE_URL=sqlite:///demo.db\n", encoding="utf-8")
        cmd_init(N(name="テスト", code="ST"))
        p = VAULT_OVERRIDE / "projects" / "odsc-selftest"
        for want in ("【未記入】",):
            try:
                cmd_odsc(N(action="lock", reason=None)); raise AssertionError("未記入の ODSC をロックできた")
            except HkError as e:
                assert want in str(e)
        try:
            cmd_new(N(title="自己診断", kind="calc")); raise AssertionError("ODSC 未ロックでタスクを作れた")
        except HkError:
            pass
        (p / "ODSC.md").write_text("# ODSC\n\n## 1. ODSC\n\n- Objective: 自己診断\n- SC1: 明細と集計が合う\n\n## 2. Customer\n\n- 自分\n", encoding="utf-8")
        cmd_odsc(N(action="lock", reason=None))
        cmd_new(N(title="自己診断", kind="calc"))
        sd, cd = p / "tasks" / "ST-001", ROOT / "tasks" / "ST-001"
        assert (sd / "spec.md").exists() and not (cd / "spec.md").exists(), "指示書が vault 側に無い"
        (sd / "spec.md").write_text("# spec\n自己診断（SC9）\n", encoding="utf-8")
        wj(sd / "checks.json", {"checks": [
            {"id": "C1", "type": "row_count", "why": "t", "file": "data/items.csv", "equals": 3},
            {"id": "C2", "type": "unique", "why": "t", "file": "data/items.csv", "columns": ["item_code"]},
            {"id": "C3", "type": "not_null", "why": "t", "file": "data/items.csv", "columns": ["name", "unit_price"]},
            {"id": "C4", "type": "foreign_key", "why": "t", "file": "data/sales.csv", "column": "item_code", "ref_file": "data/items.csv", "ref_column": "item_code"},
            {"id": "C5", "type": "sum_equals", "why": "t", "file": "data/sales.csv", "column": "amount", "expected": 1846},
            {"id": "C6", "type": "reconcile", "why": "t", "file_a": "data/sales.csv", "key": "item_code", "column_a": "amount", "file_b": "out/by_item.csv", "column_b": "amount"},
            {"id": "C8", "type": "sources_unchanged", "why": "t"},
            {"id": "C7", "type": "command", "why": "t", "cmd": f"\"{sys.executable}\" -c \"print(1)\""}]})
        assert cmd_eval(N(task="ST-001")) == 1, "未ロックなのに PASS した"
        try:
            cmd_lock(N(task="ST-001", reason=None)); raise AssertionError("ODSC に無い SC でロックできた")
        except HkError as e:
            assert "Success Criteria" in str(e), str(e)
        (sd / "spec.md").write_text("# spec\n- SC1のために出すもの: 自己診断\n", encoding="utf-8")
        cmd_lock(N(task="ST-001", reason=None))
        cmd_fetch(N(task="ST-001", name="items", sql="select * from items", sql_file=None, source="default"))
        cmd_fetch(N(task="ST-001", name="sales", sql="select * from sales", sql_file=None, source="default"))
        (ROOT / "原本.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        cmd_source(N(task="ST-001", path="原本.csv"))
        good = "item_code,amount\nA-001,1446\nA-002,400\n"
        (cd / "out" / "by_item.csv").write_text(good, encoding="utf-8")
        cmd_step(N(task="ST-001", step="S1", state="start")); cmd_step(N(task="ST-001", step="S1", state="done"))
        for ty, tx in (("fix", "集計キーを直した"), ("design", "opus"), ("park", "顧客別も見たい")):
            cmd_note(N(task="ST-001", type=ty, text=tx))
        cmd_resume(N(task=None))
        assert cmd_eval(N(task="ST-001")) == 0, "正しい成果物が FAIL した"
        keep_odsc = (p / "ODSC.md").read_text(encoding="utf-8")
        (p / "ODSC.md").write_text(keep_odsc + "\n- SC2: ついでに顧客別も\n", encoding="utf-8")
        assert cmd_eval(N(task="ST-001")) == 1, "ODSC の書き換えを見逃した"
        (p / "ODSC.md").write_text(keep_odsc, encoding="utf-8")
        (ROOT / "原本.csv").write_text("a,b\n1,3\n", encoding="utf-8")
        assert cmd_eval(N(task="ST-001")) == 1, "原本の書き換えを見逃した"
        (ROOT / "原本.csv").write_text("a,b\n1,2\n", encoding="utf-8")
        (cd / "out" / "by_item.csv").write_text("item_code,amount\nA-001,1400\nA-002,400\n", encoding="utf-8")
        assert cmd_eval(N(task="ST-001")) == 1, "誤った集計が PASS した"
        (cd / "out" / "by_item.csv").write_text(good, encoding="utf-8")
        c = rj(sd / "checks.json"); c["checks"] = c["checks"][:1]; wj(sd / "checks.json", c)
        assert cmd_eval(N(task="ST-001")) == 1, "検査を削っても PASS した（ロックが効いていない）"
        for bad in ("delete from items", "select 1; drop table items", "with x as (select 1) insert into items select * from x"):
            try:
                check_select_only(bad); raise AssertionError(f"書き込み SQL を通した: {bad}")
            except HkError:
                pass
        cmd_lock(N(task="ST-001", reason="自己診断: 検査を戻さず再ロック"))
        assert cmd_eval(N(task="ST-001")) == 0
        (cd / "src" / "run.py").write_text("import csv,collections\nt=collections.defaultdict(float)\nfor r in csv.DictReader(open('data/sales.csv',encoding='utf-8-sig')): t[r['item_code']]+=float(r['amount'])\nw=csv.writer(open('out/by_item.csv','w',newline=''));w.writerow(['item_code','amount']);[w.writerow([k,v]) for k,v in t.items()]\n", encoding="utf-8")
        before = (sd / "eval.json").read_text(encoding="utf-8")
        assert cmd_audit(N(task="ST-001", out=None)) == 0, "監査で再現できなかった"
        assert before == (sd / "eval.json").read_text(encoding="utf-8"), "監査がタスクの履歴を書き換えた"
        assert not fb_new(), "FB が無いのに未対応と出た"
        with (p / "FEEDBACK.md").open("a", encoding="utf-8") as f:
            f.write("\n## 2026-11-17 ST-001\n\n- 単位を明記すること\n")
        assert fb_new(), "FB の書き込みを検知できない"
        cmd_fb(N(action="ack", text="単位を成果物に追記した")); assert not fb_new()
        n_ev = len(rj(sd / "eval.json")["attempts"])
        cmd_close(N(task="ST-001", learning=["自己診断の学び"], abandon=None, no_push=True))
        (p / "NEXT_SESSION.md").write_text("x\n\n次は S2\n", encoding="utf-8"); write_status(); cmd_status(N())
        for name in ("REPO.json", "ODSC.md", "STATUS.md", "NEXT_SESSION.md", "LEARNINGS.md", "_LEDGER.csv", "_STEPS.csv", "FEEDBACK_RESPONSES.md",
                     "records/ST-001.md", "tasks/ST-001/log.md", "tasks/ST-001/events.jsonl"):
            assert (p / name).exists(), "vault に無い: " + name
        assert not (ROOT / "records").exists() and not (ROOT / "ODSC.md").exists(), "指示書がコード側に書かれた"
        rec = (p / "records" / "ST-001.md").read_text(encoding="utf-8")
        assert "relocks: 1" in rec and f"eval_attempts: {n_ev}" in rec and "first_pass: false" in rec and "fixes: 1" in rec and "design: opus" in rec and "parked: 1" in rec, rec[:600]
        cmd_stats(N(out=None))
        print("\nSELFTEST PASS")
        return 0
    finally:
        ROOT, VAULT_OVERRIDE = keep, keep_v
        shutil.rmtree(base, ignore_errors=True)


def main():
    p = argparse.ArgumentParser(prog="hk")
    s = p.add_subparsers(dest="cmd", required=True)
    x = s.add_parser("init"); x.add_argument("--name", required=True); x.add_argument("--code", required=True); x.set_defaults(f=cmd_init)
    x = s.add_parser("where"); x.add_argument("task", nargs="?"); x.set_defaults(f=cmd_where)
    x = s.add_parser("odsc"); x.add_argument("action", choices=["lock", "show"]); x.add_argument("--reason"); x.set_defaults(f=cmd_odsc)
    x = s.add_parser("new"); x.add_argument("title"); x.add_argument("--kind", choices=["calc", "auto"], default="calc"); x.set_defaults(f=cmd_new)
    x = s.add_parser("lock"); x.add_argument("task"); x.add_argument("--reason"); x.set_defaults(f=cmd_lock)
    x = s.add_parser("fetch"); x.add_argument("task"); x.add_argument("name"); x.add_argument("--sql"); x.add_argument("--sql-file")
    x.add_argument("--source", default="default"); x.set_defaults(f=cmd_fetch)
    x = s.add_parser("source"); x.add_argument("task"); x.add_argument("path"); x.set_defaults(f=cmd_source)
    x = s.add_parser("eval"); x.add_argument("task"); x.set_defaults(f=cmd_eval)
    x = s.add_parser("step"); x.add_argument("task"); x.add_argument("step"); x.add_argument("state", choices=["start", "done"]); x.set_defaults(f=cmd_step)
    x = s.add_parser("note"); x.add_argument("task"); x.add_argument("text"); x.add_argument("--type", choices=NOTE_TYPES, default="try"); x.set_defaults(f=cmd_note)
    x = s.add_parser("close"); x.add_argument("task"); x.add_argument("--learning", action="append"); x.add_argument("--abandon")
    x.add_argument("--no-push", action="store_true"); x.set_defaults(f=cmd_close)
    x = s.add_parser("resume"); x.add_argument("task", nargs="?"); x.set_defaults(f=cmd_resume)
    x = s.add_parser("save"); x.add_argument("text"); x.set_defaults(f=cmd_save)
    x = s.add_parser("fb"); x.add_argument("action", choices=["show", "ack"]); x.add_argument("text", nargs="?"); x.set_defaults(f=cmd_fb)
    s.add_parser("rules").set_defaults(f=cmd_rules)
    x = s.add_parser("update"); x.add_argument("--from", dest="source"); x.set_defaults(f=cmd_update)
    x = s.add_parser("audit"); x.add_argument("task"); x.add_argument("--out"); x.set_defaults(f=cmd_audit)
    x = s.add_parser("stats"); x.add_argument("--out"); x.set_defaults(f=cmd_stats)
    s.add_parser("status").set_defaults(f=cmd_status)
    s.add_parser("selftest").set_defaults(f=cmd_selftest)
    a = p.parse_args()
    try:
        sys.exit(a.f(a) or 0)
    except HkError as e:
        print(f"エラー: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
