#!/usr/bin/env python3
"""The same data as BRIEF.md, rendered as an interactive BRIEF.html.

★ Computed once, rendered twice. ★
This module decides nothing. It receives the data the renderer already put
through all four gates, which is the only reason the two artifacts cannot drift
apart. Whatever the evidence gate dropped is equally invisible here.

Self-contained on purpose: no external resources, no network, works offline,
follows the reader's light/dark preference. Interaction is native ``<details>``
plus a few lines of JavaScript for the copy buttons and the theme toggle -- a
brief you cannot open on a plane is a brief you stop trusting.
"""

from __future__ import annotations

import datetime as dt
import html as html_mod
import json
import re
from typing import Any, Dict, List, Optional

from .i18n import Catalog

__all__ = ["render_html"]

WEEKDAY_KEYS = [
    "brief.weekday.mon", "brief.weekday.tue", "brief.weekday.wed", "brief.weekday.thu",
    "brief.weekday.fri", "brief.weekday.sat", "brief.weekday.sun",
]

# (catalog key, CSS class). The class comes from the snapshot value, never from
# the translated label, so colours survive any wording change.
SIGNAL = {
    "hot": ("signal.short.hot", "hot"),
    "warm": ("signal.short.warm", "warm"),
    "cold": ("signal.short.cold", "cold"),
    "dormant": ("signal.short.dormant", "dormant"),
    "unknown": ("signal.short.unknown", "unknown"),
}

TIER_KEYS = {
    "hook": ("tier.hook.label", "tier.hook.why"),
    "skill": ("tier.skill.label", "tier.skill.why"),
    "explore": ("tier.explore.label", "tier.explore.why"),
}

BLOCKED_KEYS = {
    "me": ("blocked.me", "bl-me"), "agent": ("blocked.agent", "bl-agent"),
    "external-party": ("blocked.external_party", "bl-wait"),
    "approval": ("blocked.approval", "bl-wait"),
    "decision": ("blocked.decision", "bl-dec"), "none": ("blocked.none", "bl-none"),
}

CSS = """
*,*::before,*::after{box-sizing:border-box}
:root{
  --bg:#fbfbfa; --fg:#1c1c1a; --dim:#6b6b66; --line:#e4e3df; --card:#fff;
  --accent:#8a5a2b; --hot:#c2410c; --warm:#b45309; --cold:#64748b; --dormant:#94a3b8;
  --dec:#7c3aed; --ok:#15803d; --warn:#b91c1c; --chip:#f2f1ee;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#16161a; --fg:#e8e6e1; --dim:#9a978f; --line:#2c2c31; --card:#1e1e23;
  --accent:#d8a05a; --hot:#f97316; --warm:#f59e0b; --cold:#94a3b8; --dormant:#6b7280;
  --dec:#a78bfa; --ok:#4ade80; --warn:#f87171; --chip:#26262c;
}}
:root[data-theme=dark]{
  --bg:#16161a; --fg:#e8e6e1; --dim:#9a978f; --line:#2c2c31; --card:#1e1e23;
  --accent:#d8a05a; --hot:#f97316; --warm:#f59e0b; --cold:#94a3b8; --dormant:#6b7280;
  --dec:#a78bfa; --ok:#4ade80; --warn:#f87171; --chip:#26262c;
}
body{margin:0;background:var(--bg);color:var(--fg);
  font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB",system-ui,sans-serif;
  -webkit-font-smoothing:antialiased}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 80px}
h1{font-size:25px;margin:0 0 4px;letter-spacing:-.3px}
h2{font-size:15px;margin:34px 0 12px;color:var(--dim);font-weight:600;
   text-transform:none;letter-spacing:.02em}
.sub{color:var(--dim);font-size:13.5px;margin-bottom:6px}
.pills{display:flex;gap:6px;flex-wrap:wrap;margin:10px 0 0}
.pill{background:var(--chip);border-radius:999px;padding:3px 11px;font-size:12.5px;color:var(--dim)}
.pill b{color:var(--fg);font-weight:600}
.banner{border-left:3px solid var(--warn);background:color-mix(in srgb,var(--warn) 8%,transparent);
  padding:9px 13px;border-radius:0 6px 6px 0;margin:14px 0;font-size:13.5px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:15px 17px;margin-bottom:10px}
.top{display:flex;gap:13px;align-items:flex-start}
.num{flex:0 0 26px;height:26px;border-radius:50%;background:var(--accent);color:#fff;
  display:grid;place-items:center;font-size:13px;font-weight:700;margin-top:1px}
.t-title{font-size:16.5px;font-weight:650;line-height:1.45;margin-bottom:5px}
.meta{display:flex;gap:6px;flex-wrap:wrap;margin:7px 0}
.tag{font-size:11.5px;padding:2px 8px;border-radius:5px;background:var(--chip);color:var(--dim);
  white-space:nowrap}
.tag.who{color:var(--fg)}
.tag.hook{background:color-mix(in srgb,var(--ok) 16%,transparent);color:var(--ok)}
.tag.skill{background:color-mix(in srgb,var(--accent) 18%,transparent);color:var(--accent)}
.tag.explore{background:color-mix(in srgb,var(--dec) 16%,transparent);color:var(--dec)}
.ev{font-size:12.5px;color:var(--dim);border-left:2px solid var(--line);padding-left:9px;margin:7px 0}
.why{font-size:13.5px;color:var(--fg);opacity:.86}
.flag{font-size:12.5px;color:var(--warn);margin-top:6px}
table{width:100%;border-collapse:collapse;font-size:13.5px}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:10px;background:var(--card)}
th{text-align:left;font-weight:600;color:var(--dim);font-size:12.5px;padding:9px 12px;
   border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:10px 12px;border-bottom:1px solid var(--line);vertical-align:top}
tr:last-child td{border-bottom:none}
.pname{font-weight:600;white-space:nowrap}
.sig{font-weight:600;white-space:nowrap}
.sig.hot{color:var(--hot)}.sig.warm{color:var(--warm)}.sig.cold{color:var(--cold)}
.sig.dormant{color:var(--dormant)}.sig.dec{color:var(--dec)}
.facts{color:var(--dim);font-size:12.5px}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:8px}
details[open]{border-color:color-mix(in srgb,var(--accent) 40%,var(--line))}
summary{padding:12px 15px;cursor:pointer;list-style:none;display:flex;gap:10px;
  align-items:center;font-size:14.5px}
summary::-webkit-details-marker{display:none}
summary::before{content:"›";color:var(--dim);font-size:17px;transition:transform .15s;flex:0 0 9px}
details[open] summary::before{transform:rotate(90deg)}
.body{padding:0 15px 15px 34px;font-size:13.5px}
.kv{margin:9px 0}
.kv .k{font-size:11.5px;color:var(--dim);text-transform:uppercase;letter-spacing:.05em;margin-bottom:2px}
.ac{margin:4px 0 0;padding-left:18px}
.ac li{margin:3px 0;color:var(--dim)}
code,.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12.5px}
code{background:var(--chip);padding:1px 5px;border-radius:4px}
.cmd{display:flex;gap:8px;align-items:center;margin-top:10px;flex-wrap:wrap}
.cmd code{flex:1;min-width:190px;padding:7px 10px;background:var(--chip);border-radius:6px}
button{font:inherit;font-size:12.5px;padding:6px 13px;border-radius:6px;border:1px solid var(--line);
  background:var(--card);color:var(--fg);cursor:pointer;white-space:nowrap}
button:hover{border-color:var(--accent);color:var(--accent)}
button.copied{border-color:var(--ok);color:var(--ok)}
.note{font-size:13px;color:var(--dim);margin:-4px 0 12px}
.grid2{display:grid;gap:9px}
@media(min-width:700px){.grid2{grid-template-columns:1fr 1fr}}
ul.plain{margin:0;padding-left:19px}
ul.plain li{margin:5px 0}
.foot{margin-top:44px;padding-top:16px;border-top:1px solid var(--line);
  color:var(--dim);font-size:12px;line-height:1.7}
.toggle{position:fixed;top:14px;right:14px;z-index:9}
.dot{display:inline-block;width:7px;height:7px;border-radius:50%;margin-right:5px;vertical-align:1px}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
"""

# %s is the localised "copied" label, injected as a JSON string literal.
JS = """
function cp(b,t){navigator.clipboard.writeText(t).then(function(){
  var o=b.textContent;b.textContent=%s;b.classList.add('copied');
  setTimeout(function(){b.textContent=o;b.classList.remove('copied')},1400);});}
(function(){var r=document.documentElement,k='nextbrief-theme',s=localStorage.getItem(k);
 if(s)r.setAttribute('data-theme',s);
 document.getElementById('th').onclick=function(){
   var cur=r.getAttribute('data-theme')||
     (matchMedia('(prefers-color-scheme:dark)').matches?'dark':'light');
   var n=cur==='dark'?'light':'dark';r.setAttribute('data-theme',n);localStorage.setItem(k,n);};})();
"""

_BOLD = re.compile(r"\*\*(.+?)\*\*")
_CODE = re.compile(r"`(.+?)`")


def e(s) -> str:
    return html_mod.escape(str(s if s is not None else ""))


def md_inline(s) -> str:
    """Only ``**bold**`` and ``` `code` ``` -- the two markers the data uses.

    Escaping happens first, so any HTML that arrived from a project document is
    rendered as text. Project files are data, never markup we execute.
    """
    out = e(s)
    out = _BOLD.sub(r"<strong>\1</strong>", out)
    out = _CODE.sub(r"<code>\1</code>", out)
    return out


def _cmd(verb: str, item_id: str) -> str:
    return "nextbrief %s %s" % (verb, item_id)


def _js_str(s: str) -> str:
    """Embed a Python string as a JS literal. json.dumps escapes the quotes and
    the closing-tag sequence stays impossible because we also escape ``<``."""
    return json.dumps(str(s)).replace("<", "\\u003c")


def _attr(s: str) -> str:
    """A string safe inside a single-quoted HTML attribute holding JS source."""
    return e(str(s).replace("\\", "\\\\").replace("'", "\\'"))


def render_html(snapshot, brief, backlog, cfg, reg, cat: Catalog,
                notes: Optional[dict] = None, meta: Optional[dict] = None) -> str:
    """Render BRIEF.html.

    ``notes`` and ``meta`` are what the renderer already computed. They are
    optional only so that this function can be called on its own; when they are
    missing we call the *same* classifier the renderer uses rather than inventing
    a second opinion.
    """
    from .render import caps_of, classify   # imported late: render must not import us at module level

    notes = notes or {}
    if meta is None:
        meta = classify(snapshot, backlog, cfg, reg)
    caps = caps_of(cfg)

    run = snapshot.get("run") or {}
    gen = dt.datetime.fromisoformat(run["generated_at"])
    as_of = dt.date.fromisoformat(run["as_of_date"])
    P = {p.get("id"): p for p in (snapshot.get("projects") or [])}
    open_items = meta["open"]
    by_id = {b.get("id"): b for b in backlog}
    self_ids = meta["self_ids"]
    dec_ids, stall_ids, neg_ids = meta["decision_ids"], meta["stalled_ids"], meta["neglected_ids"]

    o: List[str] = []
    A = o.append

    A("<div class=wrap>")
    A("<button class=toggle id=th title='%s'>◐</button>" % e(cat.t("html.theme_toggle")))

    # ---------- header ----------
    A("<h1>%s</h1>" % e(cat.t("html.header.title", date=as_of.isoformat(),
                              weekday=cat.t(WEEKDAY_KEYS[as_of.weekday()]))))
    prev = notes.get("prev_run")
    if prev is None:
        st = e(cat.t("brief.header.first_run"))
    elif prev.get("ok"):
        st = "<span class='dot ok'></span>" + e(
            cat.t("html.prev_ok", at=str(prev.get("at", ""))[:16].replace("T", " ")))
    else:
        st = "<span class='dot warn'></span>" + md_inline(cat.t("html.prev_incomplete"))
    A("<div class=sub>%s</div>" % cat.t("html.sub", status=st, time=gen.strftime("%H:%M")))

    tracked = [p for p in (snapshot.get("projects") or []) if p.get("id") not in self_ids]
    pills = [(cat.t("html.pill.projects"), len(tracked))]
    if meta["decision_pending"]:
        pills.append((cat.t("html.pill.decision_pending"), len(meta["decision_pending"])))
    if meta["stalled"]:
        pills.append((cat.t("html.pill.stalled"), len(meta["stalled"])))
    if meta["neglected"]:
        pills.append((cat.t("html.pill.neglected"), len(meta["neglected"])))
    pills.append((cat.t("html.pill.open_items"), len(open_items)))
    unconf = [b for b in open_items if not b.get("human_confirmed")]
    if unconf:
        pills.append((cat.t("html.pill.unconfirmed"), len(unconf)))
    A("<div class=pills>" + "".join(
        "<span class=pill>%s <b>%d</b></span>" % (e(k), v) for k, v in pills) + "</div>")

    if run.get("late"):
        A("<div class=banner>%s</div>" % md_inline(cat.t(
            "html.banner.late", slot=run.get("planned_slot", ""),
            hours=(run.get("lateness_minutes") or 0) // 60)))
    if notes.get("dropped_claims"):
        A("<div class=banner>%s</div>" % md_inline(cat.t(
            "html.banner.dropped", count=notes["dropped_claims"], path="log/rejected.jsonl")))
    if notes.get("reverted_fields"):
        A("<div class=banner>%s</div>" % md_inline(cat.t(
            "html.banner.reverted", count=notes["reverted_fields"], path="log/rejected.jsonl")))

    # ---------- do these first ----------
    nexts = (brief or {}).get("next_actions") or []
    if nexts:
        A("<h2>%s</h2>" % e(cat.t("html.section.next_actions")))
        A("<div class=note>%s</div>" % md_inline(cat.t("html.note.next_actions")))
        for i, a in enumerate(nexts[:caps["max_next_actions"]], 1):
            A("<div class=card><div class=top><div class=num>%d</div><div style='flex:1'>" % i)
            A("<div class=t-title>%s</div>" % md_inline(a.get("title")))
            tags = []
            if a.get("estimate"):
                tags.append(("", a["estimate"]))
            if a.get("who"):
                tags.append(("who", a["who"]))
            tg = a.get("automation_tier")
            if tg in TIER_KEYS:
                tags.append((tg, cat.t(TIER_KEYS[tg][0])))
            pid = a.get("project")
            if pid in P:
                tags.append(("", P[pid].get("name", "")))
            A("<div class=meta>" + "".join(
                "<span class='tag %s'>%s</span>" % (c, e(t)) for c, t in tags) + "</div>")
            if a.get("evidence_line"):
                A("<div class=ev>%s</div>"
                  % md_inline(cat.t("brief.action.evidence_line", text=a["evidence_line"])))
            if a.get("why"):
                A("<div class=why>%s</div>" % md_inline(a["why"]))
            if a.get("non_goal_flag"):
                A("<div class=flag>%s</div>"
                  % e(cat.t("brief.action.non_goal_flag", non_goal=a["non_goal_flag"])))
            bid = a.get("backlog_id")
            if bid and bid in by_id:
                show, done = _cmd("show", bid), _cmd("done", bid)
                A("<div class=cmd><code>%s</code>"
                  "<button onclick=\"cp(this,'%s')\">%s</button>"
                  "<button onclick=\"cp(this,'%s')\">%s</button></div>"
                  % (e(show), _attr(show), e(cat.t("html.copy_button")),
                     _attr(done), e(cat.t("html.copy_done"))))
            A("</div></div></div>")

    # ---------- agent queue ----------
    agentq = [b for b in open_items
              if b.get("blocked_by") == "agent" or (b.get("automation") or {}).get("tier") == "hook"]
    if agentq:
        A("<h2>%s</h2>" % e(cat.t("html.section.agent_queue")))
        A("<div class=note>%s</div>"
          % md_inline(cat.t("html.note.agent_queue", command="nextbrief do <id>")))
        for b in agentq[:caps["max_agent_queue"]]:
            A(_item_details(b, P, cat, launchable=True))

    # ---------- projects ----------
    A("<h2>%s</h2>" % e(cat.t("html.section.projects")))
    A("<div class=scroll><table><thead><tr>"
      "<th>%s</th><th>%s</th><th>%s</th><th>%s</th></tr></thead><tbody>"
      % (e(cat.t("brief.table.project")), e(cat.t("brief.table.signal")),
         e(cat.t("brief.table.evidence")), e(cat.t("brief.table.next"))))
    for p in meta["ranked"]:
        pid = p.get("id")
        if pid in self_ids:
            continue
        ev = p.get("evidence") or {}
        if pid in dec_ids:
            sig, cls = cat.t("html.signal.decision_pending"), "dec"
        elif pid in neg_ids:
            sig, cls = cat.t("html.signal.neglected", days=ev.get("days_since")), "dormant"
        else:
            key, cls = SIGNAL.get(ev.get("signal"), SIGNAL["unknown"])
            sig = cat.t(key)
        nxt = ""
        na = [b for b in open_items
              if b.get("project") == pid and b.get("is_next_action")]
        if pid in dec_ids and not na:
            nxt = e(cat.t("html.next.decision"))
        elif p.get("has_own_daily_entry"):
            d = ((brief or {}).get("delegated") or {}).get(pid)
            nxt = e(d) if d else e(cat.t("brief.next.delegated",
                                         file=str(p["has_own_daily_entry"]).rsplit("/", 1)[-1]))
        elif na:
            nxt = "<code>%s</code> %s" % (e(na[0].get("id")), e(na[0].get("title")))
        elif pid in stall_ids:
            nxt = md_inline(cat.t("brief.next.stalled"))
        A("<tr><td class=pname>%s</td><td class='sig %s'>%s</td>"
          "<td class=facts>%s</td><td>%s</td></tr>"
          % (e(p.get("name", "")), cls, e(sig), md_inline(_facts(p, cat)), nxt))
    A("</tbody></table></div>")

    # ---------- awaiting a decision ----------
    if meta["decision_pending"]:
        A("<h2>%s</h2>" % e(cat.t("html.section.decision_pending")))
        A("<div class=note>%s</div>" % md_inline(cat.t("html.note.decision_pending")))
        for p in meta["decision_pending"]:
            od = p.get("open_decision") or {}
            A("<details open><summary><b>%s</b>　%s</summary><div class=body>"
              % (e(p.get("name", "")), e(od.get("question", ""))))
            if od.get("evidence_needed"):
                A("<div class=kv><div class=k>%s</div>%s</div>"
                  % (e(cat.t("html.label.evidence_needed")), md_inline(od["evidence_needed"])))
            if od.get("evidence_available"):
                A("<div class=kv><div class=k>%s</div>%s</div>"
                  % (e(cat.t("html.label.evidence_available")),
                     md_inline(od.get("evidence_where", ""))))
            if od.get("why_not_answered"):
                A("<div class=kv><div class=k>%s</div>%s</div>"
                  % (e(cat.t("html.label.why_not_answered")), md_inline(od["why_not_answered"])))
            note = ((brief or {}).get("decision_notes") or {}).get(p.get("id"))
            if note:
                A("<div class=kv><div class=k>%s</div>%s</div>"
                  % (e(cat.t("html.label.decision_note")), md_inline(note)))
            A("</div></details>")

    # ---------- waiting on your confirmation ----------
    if unconf:
        A("<h2>%s</h2>" % e(cat.t("html.section.unconfirmed", count=len(unconf))))
        A("<div class=note>%s</div>" % md_inline(cat.t(
            "html.note.unconfirmed", confirm="nextbrief ok <id>", drop="nextbrief drop <id>")))
        for b in unconf:
            A(_item_details(b, P, cat, launchable=False))

    # ---------- waiting on other people ----------
    waits = [b for b in open_items if b.get("blocked_by") in ("external-party", "approval")]
    ext = [p for p in (snapshot.get("projects") or [])
           if p.get("external_dependency") and p.get("id") not in stall_ids
           and p.get("id") not in self_ids]
    if waits or ext:
        A("<h2>%s</h2><div class=card><ul class=plain>" % e(cat.t("brief.section.waiting")))
        for b in waits[:caps["max_waiting_for"]]:
            A("<li>%s</li>" % md_inline(cat.t("html.waiting.item", id=b.get("id", ""),
                                              title=b.get("title", ""))))
        for p in ext:
            A("<li>%s</li>" % md_inline(cat.t("brief.waiting.project", name=p.get("name", ""),
                                              dep=p.get("external_dependency", ""))))
        A("</ul></div>")

    # ---------- reminders ----------
    rem = notes.get("reminders") or []
    if rem:
        A("<h2>%s</h2><div class=card><ul class=plain>" % e(cat.t("brief.section.reminders")))
        for r in rem[:8]:
            A("<li>%s</li>" % md_inline(r))
        A("</ul></div>")

    A("<div class=foot>%s<br>%s</div>"
      % (md_inline(cat.t("html.footer.same_data", generator="nextbrief render",
                         time=gen.strftime("%Y-%m-%d %H:%M"))),
         md_inline(cat.t("html.footer.evidence_gate"))))
    A("</div>")

    return ("<!doctype html><html lang=%s><head><meta charset=utf-8>"
            "<meta name=viewport content='width=device-width,initial-scale=1'>"
            "<title>%s</title><style>%s</style></head><body>%s"
            "<script>%s</script></body></html>"
            % (e(cat.t("html.lang")),
               e(cat.t("html.doc_title", app="nextbrief", date=as_of.isoformat())),
               CSS, "".join(o), JS % _js_str(cat.t("html.copied"))))


def _facts(p, cat: Catalog) -> str:
    """The evidence column. Same rule as the Markdown brief: name the kind of
    signal, never launder a file mtime into something that sounds like a commit."""
    bits = []
    g = (p.get("git") or [{}])[0]
    fs = p.get("fs") or {}
    changed = fs.get("changed") or {}
    if (g.get("commits_since") or {}).get("30"):
        bits.append(cat.t("evidence.commits_30d", count=g["commits_since"]["30"]))
    if g.get("last_commit"):
        bits.append(cat.t("evidence.last_commit", date=g["last_commit"].get("date", "")))
    if g.get("uncommitted"):
        bits.append(cat.t("evidence.uncommitted", count=g["uncommitted"]))
    if changed.get("7"):
        bits.append(cat.t("evidence.files_7d", count=changed["7"]))
    if fs.get("distinct_active_days_30d"):
        bits.append(cat.t("evidence.active_days_30d.compact",
                          count=fs["distinct_active_days_30d"]))
    s = p.get("sessions") or {}
    if s.get("distinct_session_days"):
        bits.append(cat.t("evidence.session_days.compact", count=s["distinct_session_days"]))
    if not bits:
        bits.append(cat.t("evidence.no_signal_since",
                          date=(p.get("evidence") or {}).get("best_date")
                          or cat.t("evidence.unknown_date")))
    if p.get("git_declared") == "none":
        bits.append(cat.t("evidence.caveat_no_git"))
    return cat.t("sep.dot").join(bits)


def _item_details(b: Dict[str, Any], P: Dict[Any, Any], cat: Catalog, launchable: bool) -> str:
    """One collapsible backlog entry: what an agent could do, what only you can
    do, and the cheapest next probe. The detail belongs here rather than in the
    brief so the brief stays the length of a decision, not of a report."""
    a = b.get("automation") or {}
    bid = b.get("id") or ""
    tier = a.get("tier")
    o: List[str] = []
    lbl_key, _cls = BLOCKED_KEYS.get(b.get("blocked_by"), ("", ""))
    o.append("<details><summary><code>%s</code><span style='flex:1'>%s</span>"
             % (e(bid), e(b.get("title"))))
    if tier in TIER_KEYS:
        o.append("<span class='tag %s'>%s</span>" % (tier, e(cat.t(TIER_KEYS[tier][0]))))
    if lbl_key:
        o.append("<span class=tag>%s</span>" % e(cat.t(lbl_key)))
    o.append("</summary><div class=body>")

    pid = b.get("project")
    if pid in P:
        o.append("<div class=kv><div class=k>%s</div>%s</div>"
                 % (e(cat.t("html.label.project")), e(P[pid].get("name", ""))))
    if tier in TIER_KEYS:
        o.append("<div class=kv><div class=k>%s</div>%s%s%s</div>"
                 % (e(cat.t("html.label.automation_tier")), e(cat.t(TIER_KEYS[tier][0])),
                    e(cat.t("sep.dash")), e(cat.t(TIER_KEYS[tier][1]))))
    if a.get("what_agent_can_do"):
        o.append("<div class=kv><div class=k>%s</div>%s</div>"
                 % (e(cat.t("html.label.agent_can_do")), md_inline(a["what_agent_can_do"])))
    if a.get("what_needs_human"):
        o.append("<div class=kv><div class=k>%s</div>%s</div>"
                 % (e(cat.t("html.label.human_must_do")), md_inline(a["what_needs_human"])))
    if a.get("next_probe"):
        o.append("<div class=kv><div class=k>%s</div>%s</div>"
                 % (e(cat.t("html.label.next_probe")), md_inline(a["next_probe"])))
    body = b.get("_body") or ""
    acs = [ln for ln in body.splitlines() if ln.strip().startswith("- [")]
    if acs:
        o.append("<div class=kv><div class=k>%s</div><ul class=ac>"
                 % e(cat.t("html.label.acceptance")))
        for ln in acs:
            o.append("<li>%s</li>" % md_inline(ln.strip().lstrip("- [ ]x").strip()))
        o.append("</ul></div>")
    src = b.get("source") or {}
    if src.get("doc"):
        stale = ""
        if src.get("source_last_updated_declared"):
            stale = e(cat.t("html.source.declared", date=src["source_last_updated_declared"]))
        o.append("<div class=kv><div class=k>%s</div><code>%s</code> %s %s</div>"
                 % (e(cat.t("html.label.source")), e(src["doc"]), e(src.get("anchor", "")), stale))

    cmds = []
    if launchable:
        cmds.append((_cmd("do", bid), cat.t("html.copy_launch")))
    cmds.append((_cmd("ok", bid), cat.t("html.copy_confirm")))
    cmds.append((_cmd("done", bid), cat.t("html.copy_finish")))
    o.append("<div class=cmd><code>%s</code>%s</div>"
             % (e(cmds[0][0]),
                "".join("<button onclick=\"cp(this,'%s')\">%s</button>" % (_attr(c), e(t))
                        for c, t in cmds)))
    o.append("</div></details>")
    return "".join(o)
