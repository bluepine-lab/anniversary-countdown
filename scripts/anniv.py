#!/usr/bin/env python3
"""記念日スキルの計算スクリプト(Python標準ライブラリのみ)。

  python anniv.py add 名前 日付 [--種別 誕生日] [--型 毎年|起点|両方] [--メモ 文]
  python anniv.py list
  python anniv.py remove 名前
  python anniv.py today [--date YYYY-MM-DD] [--days 30]
  python anniv.py show 名前 [--date YYYY-MM-DD]
  python anniv.py kiri [--date YYYY-MM-DD] [--days 400]   # これから来る「数字が面白い日」だけ
  python anniv.py frame "見出し" [--調子 祝う|静か|ふつう] [--行 "他の行"]...   # 一番長い行の表示幅に合わせた枠の罫線
  python anniv.py page --out ページ.html [--date] [--days] [--ひとこと祝う 文] [--ひとことふつう 文] [--ひとこと静か 文]   # 近い記念日をHTMLページに書き出す
  python anniv.py check

日付は YYYY-MM-DD(年が分からない毎年の記念日は MM-DD)。
型: 毎年=毎年くる日(誕生日・結婚記念日)/ 起点=その日から数える(付き合った日・開業日。○日目)/ 両方。
計算はこのスクリプトが行い、文章(お祝い・ひとこと)は呼び出し側のAIが書く。
占いスキル(koyomi.py)が近くにあれば、暦注(一粒万倍日・天赦日など)も付ける。無ければ付けない。
保存先は ~/.claude/記念日/anniversaries.json(環境変数 ANNIV_HOME で変更可)。保存するのは入力内容だけ。
"""
import argparse
import datetime
import json
import os
import sys
from pathlib import Path

# 起点型の節目(○日目)。起算日を1日目と数える。キリ番・ぞろ目・語呂合わせを「面白い日」とする。
GORO = {777: "ラッキー7が3つ", 1234: "数字が順に並ぶ", 2525: "ニコニコ", 3939: "サンキュー",
        4649: "よろしく"}
DANKAI = [  # (あと何日以下か, 段階名)。小さい方から当てはめる
    (0, "当日"), (1, "前日"), (7, "直前"), (21, "準備"), (10**6, "計画")]
# 段階ごとの「次の一手」の候補(AIが状況に合わせて選び、言い換える)。型は種別から決まる
ICHITE = {
    "誕生日": {"計画": ["贈り物の候補を1つ決める", "お祝いの場所や集まる人を考える"],
              "準備": ["贈り物を注文・購入する", "食事や集まりの予約をする"],
              "直前": ["予約と贈り物の最終確認", "メッセージの下書きをする"],
              "前日": ["贈り物・カードを手元に揃える", "連絡の準備をする"],
              "当日": ["おめでとうを直接、または一言で伝える"]},
    "ふたり": {"計画": ["行きたい場所や食事を1つ決める", "写真を見返してみる"],
              "準備": ["お店や宿を予約する", "贈り物か手紙を用意する"],
              "直前": ["予約の確認、服や持ち物の準備"],
              "前日": ["贈り物・花を受け取る手配をする"],
              "当日": ["ありがとうを一言、言葉にして伝える"]},
    "事業": {"計画": ["この1年(期間)の振り返りメモを作り始める"],
            "準備": ["数字と出来事を3つずつ書き出す", "お世話になった人へのお礼を考える"],
            "直前": ["振り返りと次の目標を1行にまとめる"],
            "前日": ["お礼の連絡を準備する"],
            "当日": ["ここまでの歩みを小さく祝い、次の一手を1つ決める"]},
    "一般": {"計画": ["その日にしたいことを1つ決める"],
            "準備": ["必要な予約や準備を洗い出す"],
            "直前": ["予定と準備の最終確認"],
            "前日": ["持ち物や連絡の準備をする"],
            "当日": ["その日を少し特別に過ごす"]},
}
KINDS = ("毎年", "起点", "両方")
SOON_DAYS = 30



def date_arg(text):
    """argparse用: 日付の指定ミスを日本語で知らせる。"""
    try:
        return datetime.date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError("日付は YYYY-MM-DD の形で、実在する日を指定してください(入力: %s)" % text)


def home_dir():
    base = os.environ.get("ANNIV_HOME")
    return Path(base) if base else Path.home() / ".claude" / "記念日"


def store_path():
    return home_dir() / "anniversaries.json"


def load():
    p = store_path()
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save(items):
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")


def parse_date(text):
    """'YYYY-MM-DD' は (年, 月, 日)、'MM-DD' は (None, 月, 日)。実在しない日付は例外。"""
    parts = text.strip().replace("/", "-").split("-")
    try:
        nums = [int(x) for x in parts]
    except ValueError:
        raise ValueError("日付が読み取れません(例: 2020-05-17 / 05-17): %s" % text)
    if len(nums) == 3:
        y, m, d = nums
        datetime.date(y, m, d)
    elif len(nums) == 2:
        y, (m, d) = None, nums
        datetime.date(2000, m, d)  # 2000年は閏年なので2/29も通す
    else:
        raise ValueError("日付が読み取れません(例: 2020-05-17 / 05-17): %s" % text)
    return y, m, d


def _leap(y):
    return y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)


def on_year(year, m, d):
    """その年の記念日。2/29は閏年でない年は2/28に寄せる。"""
    if m == 2 and d == 29 and not _leap(year):
        return datetime.date(year, 2, 28)
    return datetime.date(year, m, d)


def next_yearly(item, today):
    """毎年型: 今日以降で最も近い記念日(今日を含む)と、その回数(歳・周年)。年不明なら回数None。"""
    y0, m, d = parse_date(item["日付"])
    this = on_year(today.year, m, d)
    nxt = this if this >= today else on_year(today.year + 1, m, d)
    count = nxt.year - y0 if y0 is not None else None
    return nxt, count


def day_number(item, today):
    """起点型: 起算日を1日目とした今日の通し番号。年が未登録ならNone。"""
    y0, m, d = parse_date(item["日付"])
    if y0 is None:
        return None
    return (today - datetime.date(y0, m, d)).days + 1


def notable(n):
    """○日目が「面白い日」ならその理由、そうでなければNone(キリ番・ぞろ目・語呂)。"""
    if n < 50:
        return None
    s = str(n)
    if n in GORO:
        return "%s(語呂・並び)" % GORO[n]
    if len(s) >= 3 and len(set(s)) == 1:
        return "ぞろ目"
    if n % 1000 == 0:
        return "キリ番(%d千日)" % (n // 1000)
    if n == 50 or (n % 100 == 0 and n <= 1000):
        return "キリ番"
    return None


def milestones_after(item, today, limit=1, horizon=30000):
    """起点型: 今日より後(今日は含まない)の「面白い日」を近い順にlimit件。"""
    y0, m, d = parse_date(item["日付"])
    if y0 is None:
        return []
    start = datetime.date(y0, m, d)
    n = (today - start).days + 1
    found = []
    for k in range(max(n + 1, 1), horizon):
        why = notable(k)
        if why:
            day = start + datetime.timedelta(days=k - 1)
            found.append({"日目": k, "日付": day.isoformat(), "あと日数": (day - today).days, "理由": why})
            if len(found) >= limit:
                break
    return found


def next_milestone(item, today):
    r = milestones_after(item, today)
    return r[0] if r else None


def _koyomi_module():
    """占いスキルの暦計算(koyomi.py)が近くにあれば読み込む。無ければNone。"""
    home = os.path.expanduser("~")
    here = os.path.dirname(os.path.abspath(__file__))
    for p in (os.path.join(here, "..", "..", "占い", "scripts"),
              os.path.join(home, ".claude", "skills", "占い", "scripts"),
              os.path.join(home, ".gemini", "skills", "占い", "scripts"),
              os.path.join(home, ".agents", "skills", "占い", "scripts")):
        if os.path.exists(os.path.join(p, "koyomi.py")):
            sys.path.insert(0, p)
            try:
                import koyomi
                return koyomi
            except Exception:
                return None
            finally:
                sys.path.remove(p)
    return None


def day_quality(koyomi, day):
    """その日の暦注から、'吉'(一粒万倍日・天赦日)/'凶'(不成就日)/None と、特別な日の名前を返す。"""
    names = [s.split("(")[0] for s in koyomi.koyomi(day)["特別な日"]]
    if "天赦日" in names:
        return "吉", names
    if "一粒万倍日" in names:
        return "吉", names
    if "不成就日" in names:
        return "凶", names
    return None, names


def koyomi_note(koyomi, day, window=7):
    """記念日の日の暦注。吉でなければ、前後window日で一番近い吉日(天赦日優先)も添える。"""
    q, names = day_quality(koyomi, day)
    note = {"その日の暦注": [n for n in names if n in ("天赦日", "一粒万倍日", "不成就日")] or ["特になし"]}
    if q == "吉":
        note["判定"] = "吉日と重なる"
        return note
    note["判定"] = "不成就日と重なる" if q == "凶" else "吉日とは重ならない"
    near = []
    for k in range(1, window + 1):
        for sign in (-1, 1):
            cand = day + datetime.timedelta(days=sign * k)
            cq, cn = day_quality(koyomi, cand)
            if cq == "吉":
                near.append((k, 0 if "天赦日" in cn else 1, sign, cand, cn))
    if near:
        k, _, sign, cand, cn = min(near, key=lambda x: (x[0], x[1], -x[2]))
        note["近くの吉日"] = {"日付": cand.isoformat(), "差": "%d日%s" % (k, "前" if sign < 0 else "後"),
                              "暦注": [n for n in cn if n in ("天赦日", "一粒万倍日")]}
    return note


def ichite_kind(item):
    s = item.get("種別", "")
    if "誕生" in s:
        return "誕生日"
    if any(w in s for w in ("結婚", "付き合", "交際", "婚約", "入籍", "デート", "夫婦")):
        return "ふたり"
    if any(w in s for w in ("開業", "創業", "設立", "会社", "事業", "店")):
        return "事業"
    return "一般"


def ichite(item, days_left):
    """あと日数に応じた段階と、次の一手の候補。"""
    stage = next(name for limit, name in DANKAI if days_left <= limit)
    return {"段階": stage, "候補": ICHITE[ichite_kind(item)][stage]}


def describe(item, today, koyomi=None):
    kind = item.get("型", "毎年")
    out = {"名前": item["名前"], "種別": item.get("種別", "記念日"), "日付": item["日付"], "型": kind}
    if item.get("メモ"):
        out["メモ"] = item["メモ"]
    if kind in ("毎年", "両方"):
        nxt, count = next_yearly(item, today)
        y = {"次の日": nxt.isoformat(), "あと日数": (nxt - today).days}
        if count is not None:
            y["回数"] = count
            y["呼び方"] = ("%d歳" % count) if out["種別"] == "誕生日" else ("%d周年" % count)
        if parse_date(item["日付"])[1:] == (2, 29) and not _leap(nxt.year):
            y["注"] = "2/29の記念日は、閏年でない年は2/28に数える"
        y["次の一手"] = ichite(item, y["あと日数"])
        if koyomi:
            y["暦"] = koyomi_note(koyomi, nxt)
        out["毎年"] = y
    if kind in ("起点", "両方"):
        n = day_number(item, today)
        if n is None:
            out["起点"] = {"注": "起算日の年が未登録のため日数は数えられません"}
        elif n < 1:
            out["起点"] = {"注": "起算日は未来です", "あと日数": 1 - n}
        else:
            s = {"日目": n, "経過日数": n - 1}
            if notable(n):
                s["今日が節目"] = notable(n)
            nms = milestones_after(item, today, limit=3)
            if nms:
                s["次の面白い日"] = nms
                s["次の一手"] = ichite(item, nms[0]["あと日数"])
                if koyomi:
                    s["暦"] = koyomi_note(koyomi, datetime.date.fromisoformat(nms[0]["日付"]))
            out["起点"] = s
    return out


def upcoming(items, today, days, koyomi=None, only_milestones=False):
    """今日〜days日以内に来る記念日(毎年型)と節目(起点型)を日付順に。"""
    events = []
    for it in items:
        kind = it.get("型", "毎年")
        if kind in ("毎年", "両方") and not only_milestones:
            nxt, count = next_yearly(it, today)
            if (nxt - today).days <= days:
                events.append((nxt, it, "毎年", count, None))
        if kind in ("起点", "両方"):
            n = day_number(it, today)
            if n and notable(n):
                events.append((today, it, "節目", n, notable(n)))
            for nm in milestones_after(it, today, limit=50):
                if nm["あと日数"] > days:
                    break
                events.append((datetime.date.fromisoformat(nm["日付"]), it, "節目", nm["日目"], nm["理由"]))
    events.sort(key=lambda e: e[0])
    res = []
    for day, it, ev, n, why in events:
        if ev == "毎年":
            unit = "歳" if it.get("種別") == "誕生日" else "周年"
            what = ("%d%s" % (n, unit)) if n is not None else "毎年の記念日"
        else:
            what = "%d日目(%s)" % (n, why)
        left = (day - today).days
        e = {"日付": day.isoformat(), "あと日数": left, "名前": it["名前"],
             "種別": it.get("種別", "記念日"), "内容": what}
        if left <= 60:
            e["次の一手"] = ichite(it, left)
        if koyomi:
            e["暦"] = koyomi_note(koyomi, day)
        res.append(e)
    return res


def find(items, name):
    hit = [it for it in items if it["名前"] == name] or [it for it in items if name in it["名前"]]
    if not hit:
        raise ValueError("見つかりません: %s" % name)
    if len(hit) > 1:
        raise ValueError("複数見つかりました: %s" % "、".join(h["名前"] for h in hit))
    return hit[0]


def cmd_today(args):
    if args.days < 0:
        raise ValueError("--days は0以上で指定してください")
    items = load()
    if not items:
        return {"今日": args.date.isoformat(), "登録数": 0, "案内": "まだ記念日が登録されていません"}
    ev = upcoming(items, args.date, args.days, koyomi=_koyomi_module())
    return {"今日": args.date.isoformat(), "登録数": len(items),
            "今日の記念日": [e for e in ev if e["あと日数"] == 0],
            "近日(%d日以内)" % args.days: [e for e in ev if e["あと日数"] > 0]}


def cmd_kiri(args):
    """これから来る「数字が面白い日」(ぞろ目・キリ番・語呂)を、全登録から近い順に。"""
    ev = upcoming(load(), args.date, args.days, koyomi=_koyomi_module(), only_milestones=True)
    return {"今日": args.date.isoformat(), "範囲": "%d日以内" % args.days, "面白い日": ev}


def cmd_add(args):
    y, m, d = parse_date(args.日付)
    if args.型 in ("起点", "両方") and y is None:
        raise ValueError("起点・両方の型には、年を含む日付(YYYY-MM-DD)が必要です")
    items = load()
    if any(it["名前"] == args.名前 for it in items):
        raise ValueError("同じ名前がすでにあります: %s(先に remove してください)" % args.名前)
    it = {"名前": args.名前, "日付": args.日付.replace("/", "-"), "種別": args.種別, "型": args.型}
    if args.メモ:
        it["メモ"] = args.メモ
    items.append(it)
    save(items)
    return {"登録": it, "登録数": len(items)}


def cmd_remove(args):
    items = load()
    it = find(items, args.名前)
    items.remove(it)
    save(items)
    return {"削除": it["名前"], "登録数": len(items)}


def cmd_list(args):
    items = load()
    return {"登録数": len(items), "一覧": items}


def cmd_show(args):
    return describe(find(load(), args.名前), args.date, koyomi=_koyomi_module())


def cmd_check(args):
    """自己点検: 閏日・節目・当日の計算が正しいか。"""
    d = datetime.date
    cases = [
        (on_year(2027, 2, 29) == d(2027, 2, 28), "閏年でない年の2/29は2/28"),
        (on_year(2028, 2, 29) == d(2028, 2, 29), "閏年の2/29はそのまま"),
        (day_number({"日付": "2026-01-01"}, d(2026, 1, 1)) == 1, "起算日は1日目"),
        (day_number({"日付": "2026-01-01"}, d(2026, 4, 10)) == 100, "2026-01-01の100日目は4/10"),
        (next_yearly({"日付": "2000-05-17"}, d(2026, 5, 17)) == (d(2026, 5, 17), 26), "当日は0日後・26回目"),
        (next_yearly({"日付": "2000-05-17"}, d(2026, 5, 18)) == (d(2027, 5, 17), 27), "翌日は来年"),
        (next_milestone({"日付": "2026-01-01"}, d(2026, 4, 10))["日目"] == 111, "100日目の次の節目は111日目(ぞろ目)"),
        (notable(111) == "ぞろ目" and notable(1111) == "ぞろ目" and notable(777) is not None, "ぞろ目・ラッキー7"),
        (notable(150) is None and notable(1000) is not None and notable(4649) is not None, "ふつうの日は節目にしない"),
        (ichite({"種別": "誕生日"}, 0)["段階"] == "当日" and ichite({"種別": "誕生日"}, 30)["段階"] == "計画", "次の一手の段階"),
    ]
    for good, label in cases:
        print(("OK  " if good else "NG  ") + label)
    sys.exit(0 if all(g for g, _ in cases) else 1)


# --- ページ(HTML)の書き出し ---
QUIET_WORDS = ("命日", "追悼", "法要", "回忌", "忌日", "供養")
CELEBRATE_KINDS = ("誕生日", "結婚記念日", "付き合った日", "開業日", "ふたり", "事業", "結婚", "記念日")
TONE_MOOD = {"祝う": "もうすぐ、お祝いの日", "ふつう": "近い記念日", "静か": "静かに迎える日"}


def tone_of(ev):
    """種別・名前の言葉から、枠の調子(祝う/静か/ふつう)を決める。迷ったら重い日かもしれないものは静かへ寄せる。"""
    text = "%s %s" % (ev.get("種別", ""), ev.get("名前", ""))
    if any(w in text for w in QUIET_WORDS):
        return "静か"
    if any(w in text for w in CELEBRATE_KINDS):
        return "祝う"
    return "ふつう"


def _upright(text):
    """縦書きで数字を横向きに組むため、数字の連なりに印を付ける(1〜2桁=縦中横、3桁以上=正立)。"""
    import html
    import re
    return re.sub(r"\d+", lambda m: '<span class="%s">%s</span>' % ("tcy" if len(m.group()) <= 2 else "up", m.group()),
                  html.escape(text))


def _page_item(ev, tone):
    import html
    e = html.escape
    n = ev["あと日数"]
    when = '<b>今日</b>' if n == 0 else 'あと<b>%d</b>日' % n
    what = ev.get("内容", "")
    cal = ""
    if tone != "静か" and ev.get("暦", {}).get("判定") == "吉日と重なる":
        cal = '<span class="cal">%sと重なります</span>' % e("・".join(ev["暦"]["その日の暦注"]))
    return ('<li class="item"><span class="when">%s</span><span><span class="name">%s</span>'
            '<span class="what">%s</span>%s</span></li>' % (when, _upright(ev["名前"]), _upright(what), cal))


def render_page(result, today, hitokoto):
    """今日の様子(today の結果)をHTMLページにする。hitokoto は {調子: ひとこと文}。"""
    import html
    e = html.escape
    events = list(result.get("今日の記念日", []))
    for k, v in result.items():
        if k.startswith("近日"):
            events += v
    groups = {"祝う": [], "ふつう": [], "静か": []}
    for ev in events:
        groups[tone_of(ev)].append(ev)
    shown = [t for t in ("祝う", "ふつう", "静か") if groups[t]]
    if not shown:
        return None
    youbi = "月火水木金土日"[today.weekday()]
    k = max(len(groups[t][:3]) for t in shown)                      # 額の中の件数(画面の高さに合わせる計算に使う)
    n_long = max(len(ev["名前"]) for t in shown for ev in groups[t][:3])  # 一番長い名前の文字数(縦書きの文字の大きさに使う)
    frames = []
    for i, t in enumerate(shown):
        head = ""
        if i == 0:
            head = ('<header class="head"><h1>記念日</h1><span class="date">%d年%d月%d日(%s)</span></header>'
                    % (today.year, today.month, today.day, youbi))
        items = "".join(_page_item(ev, t) for ev in groups[t][:3])
        row = ""
        if hitokoto.get(t):
            row = ('<dl class="rows"><div class="row"><dt>ひとこと</dt><span class="lead"></span><dd>%s</dd></div></dl>'
                   % e(hitokoto[t]))
        frames.append('  <section class="frame tone-%s">%s<p class="mood">%s</p><ol class="items">%s</ol>%s</section>'
                      % (t, head, e(TONE_MOOD[t]), items, row))
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "page.html"), encoding="utf-8") as f:
        tpl = f.read()
    return tpl.replace("{{SOLO}}", " solo" if len(shown) == 1 else "").replace("{{MODE}}", (" mode-" + shown[0]) if shown in (["祝う"], ["静か"]) else "").replace("{{FRAMES}}", "\n".join(frames)).replace("{{K}}", str(k)).replace("{{N}}", str(n_long))


def cmd_page(args):
    items = load()
    if not items:
        return {"案内": "まだ記念日が登録されていません"}
    ev = upcoming(items, args.date, args.days, koyomi=_koyomi_module())
    result = {"今日の記念日": [x for x in ev if x["あと日数"] == 0],
              "近日": [x for x in ev if x["あと日数"] > 0]}
    text = render_page(result, args.date, {"祝う": args.ひとこと祝う, "ふつう": args.ひとことふつう, "静か": args.ひとこと静か})
    if text is None:
        return {"案内": "近い記念日はありません(ページは書き出していません)"}
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(text)
    return {"書き出し": args.out, "調子": [t for t in ("祝う", "ふつう", "静か") if any(tone_of(x) == t for x in ev)]}


def display_width(text):
    """表示幅(全角・絵文字=2、半角=1)。枠の長さを見出しに合わせるために使う。"""
    import unicodedata
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


# 調子ごとの枠: (左上, 右上, 左下, 右下, 横線, 行頭の縦線)。静かな日は黒い四角の「黒枠」
FRAMES = {"祝う": ("╭", "╮", "╰", "╯", "─", "┃"),
          "静か": ("■", "■", "■", "■", "■", "■"),
          "ふつう": ("┌", "┐", "└", "┘", "─", "│")}
FRAME_MAX = 60  # 枠の最大幅。これより長い行は、書く側が折り返して短くする


def cmd_frame(args):
    tl, tr, bl, br, h, bar_ch = FRAMES[args.調子]
    lines = [args.見出し] + list(args.行 or [])
    longest = max(display_width(x) for x in lines)
    n = max(3 + longest + 3, 24)
    over = [x for x in lines if 3 + display_width(x) + 3 > FRAME_MAX]
    n = min(n, FRAME_MAX)
    bar = h * n
    out = {"調子": args.調子, "上": tl + bar + tr, "見出し": "   " + args.見出し,
           "下": bl + bar + br, "閉じ": bar, "行頭の縦線": bar_ch, "一番長い行の幅": longest}
    if over:
        out["注意"] = "枠の最大幅(%d)を超える行があります。折り返して短くしてから、もう一度測ってください: %s" % (FRAME_MAX, over)
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    to_date = date_arg
    p = sub.add_parser("add")
    p.add_argument("名前")
    p.add_argument("日付")
    p.add_argument("--種別", default="記念日")
    p.add_argument("--型", choices=KINDS, default="毎年")
    p.add_argument("--メモ", default="")
    p.set_defaults(fn=cmd_add)
    p = sub.add_parser("remove")
    p.add_argument("名前")
    p.set_defaults(fn=cmd_remove)
    p = sub.add_parser("list")
    p.set_defaults(fn=cmd_list)
    p = sub.add_parser("today")
    p.add_argument("--date", type=to_date, default=datetime.date.today())
    p.add_argument("--days", type=int, default=SOON_DAYS)
    p.set_defaults(fn=cmd_today)
    p = sub.add_parser("kiri")
    p.add_argument("--date", type=to_date, default=datetime.date.today())
    p.add_argument("--days", type=int, default=400)
    p.set_defaults(fn=cmd_kiri)
    p = sub.add_parser("show")
    p.add_argument("名前")
    p.add_argument("--date", type=to_date, default=datetime.date.today())
    p.set_defaults(fn=cmd_show)
    p = sub.add_parser("page", help="近い記念日をHTMLページ(Artifact用)に書き出す")
    p.add_argument("--date", type=to_date, default=datetime.date.today())
    p.add_argument("--days", type=int, default=SOON_DAYS)
    p.add_argument("--out", required=True)
    p.add_argument("--ひとこと祝う", default="")
    p.add_argument("--ひとことふつう", default="")
    p.add_argument("--ひとこと静か", default="")
    p.set_defaults(fn=cmd_page)
    p = sub.add_parser("frame")
    p.add_argument("見出し")
    p.add_argument("--調子", choices=list(FRAMES), default="祝う")
    p.add_argument("--行", action="append", help="枠の中に入れる他の行(件の行・ひとこと)。何度でも指定できる")
    p.set_defaults(fn=cmd_frame)
    p = sub.add_parser("check")
    p.set_defaults(fn=cmd_check)
    args = ap.parse_args()
    try:
        result = args.fn(args)
    except (ValueError, json.JSONDecodeError) as e:
        print(json.dumps({"エラー": str(e)}, ensure_ascii=False))
        sys.exit(1)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
