"""What a backlog item's *state* means.

Neither :mod:`nextbrief.frontmatter` (which only reads lines) nor
:mod:`nextbrief.render` (which judges projects) owns this, and both need it. Two
things live here, and they exist for the same reason:

**A closed item is a lost item.** The moment something stops being open is the
moment it carries the most information -- what was actually done, how it differed
from what the entry said, and what it uncovered on the way -- and it is the last
moment anyone will ever be in a position to say so. A single boolean consumed all
of that.

* **defer.** The most common real state change is neither "finished" nor
  "abandoned": it is *still true, just not now*. With only ``done`` and ``drop``
  available, that state had to be recorded as one of two lies. A deferred item is
  hidden from the brief until its date arrives and then comes back on its own --
  see ``is_live``, which is the entire mechanism. **A defer that cannot return is
  a silent drop**, so every path here fails towards the item reappearing.

* **the closing record.** ``summary`` (what actually happened) and
  ``future_work`` (what this uncovered that does not belong to it), written into
  the item's own file. No new store: a done entry stays in ``backlog/`` forever
  and is already in git, so the only thing that was missing was somewhere to put
  the words.

The closing block is parsed from the body rather than the frontmatter because
both fields are prose -- a multi-paragraph summary and a list of sentences -- and
the frontmatter subset holds neither without mangling them.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple

__all__ = [
    "OPEN_STATUSES", "TERMINAL_STATUSES", "DEFERRED", "HUMAN_ONLY_STATUSES",
    "AC_OPEN", "AC_DONE", "AC_DROPPED", "AC_YOU", "AC_AGENT",
    "AC_BEGIN", "AC_END",
    "ac_owner", "ac_span", "ac_lines", "ac_progress", "needs_you",
    "ac_trace", "untraceable_acs",
    "status_of", "defer_due", "is_live", "is_parked", "days_until_due",
    "Closing", "FutureWork", "CLOSING_BEGIN", "CLOSING_END",
    "SUMMARY_HUMAN", "SUMMARY_DRAFT", "SUMMARY_NONE",
    "parse_closing", "render_closing", "upsert_closing", "record_promotion",
    "next_item_id", "id_shape", "slug", "new_item_text", "blank_item_text",
    "NOTES_END", "append_note",
    "IN_PROGRESS", "CLAIM", "CLAIM_KEYS", "claim_of", "claim_lines", "claim_age_days",
]

OPEN_STATUSES = ("open", "in_progress", "waiting")
TERMINAL_STATUSES = ("done", "dropped")
DEFERRED = "deferred"

# Statuses only a person may move an item INTO. `deferred` belongs here for the
# same reason the terminal two do: it takes an item off the page. An agent that
# could park something would be able to hide work it did not want to be asked
# about, which is the false-completion failure wearing a different word.
HUMAN_ONLY_STATUSES = TERMINAL_STATUSES + (DEFERRED,)

# The three marks an acceptance criterion can carry.
#
# Two boxes cannot say what happens after a design change. A criterion the design
# moved past is neither done nor undone, and both existing marks are a lie about
# it: `[x]` claims work that never happened, and `[ ]` claims work is outstanding
# that nobody intends to do. The second lie does not sit still -- an unticked
# criterion is what `done` drafts as `future_work`, and `followup` turns that
# into a real backlog item, so the mistake walks downstream and mints a task for
# work somebody deliberately abandoned.
#
# `~` is a MARK, not a deletion. The line stays in the file and so does its text,
# exactly as `drop` keeps an item's file and its git history. Rewriting the
# sentence would erase the one thing worth keeping: that the goal moved.
#
# They live here rather than in `cli` because `launch` needs them too: the
# session prompt quotes the criteria at an agent, and a criterion that was set
# aside must not arrive as part of the definition of done.
AC_OPEN = " "
AC_DONE = "x"
AC_DROPPED = "~"

# Who can SAY whether a criterion is met, written into the criterion's own text
# right after its number:  `- [ ] #4 (you) the brief reads right on a phone`.
#
# ★ The question is "who can tell that it is true", not "who does the work". ★
# Those come apart constantly: only a person can choose the illustrations, but
# "three files appeared in assets/" is something one command can see, so that
# criterion belongs to the agent.
#
# Counted on a real week: across three items that could not be closed, 20
# criteria, of which exactly 2 needed the author -- one UAT, one set of
# credentials. The other 18 were things a command could settle, and they sat in
# the same list, in the same shape, in front of the same person. The cost was
# never the ticking. It was that "which of these actually need me" had to be
# worked out again from scratch every single time, and that recomputation is the
# switch this tool exists to spend rather than charge.
AC_YOU = "you"
AC_AGENT = "agent"

_AC_OWNER = re.compile(r"^(?:#\d+\s*)?\((you|agent)\)", re.IGNORECASE)

# The edge of the criteria. `_item_text` has written both since items had
# bodies, so every file this engine mints already carries them -- they were
# simply never read, which is how the whole body ended up being the scan range.
AC_BEGIN = "<!-- AC:BEGIN -->"
AC_END = "<!-- AC:END -->"


def ac_owner(text: str) -> Optional[str]:
    """``"you"``, ``"agent"``, or ``None`` for a criterion carrying no marker."""
    m = _AC_OWNER.match(text.strip())
    return m.group(1).lower() if m else None


def needs_you(text: str) -> bool:
    """Whether this criterion is one to put in front of a person.

    ★ Unmarked counts as yours, and that is the load-bearing half. ★

    An unmarked criterion is not the agent's -- it is one nobody has classified
    yet, and *every criterion written before the marker existed is unmarked*.
    Reading the absence as "the agent's" would empty the tick selector for the
    entire existing backlog in one move, and empty is the one thing it must never
    be: `done` could not ask at all until recently, measured at 1 ticked box
    across 25 items, and being askable is the whole point of the step. So the
    default is to ask, and `check` reports how many are still unclassified rather
    than the engine guessing on their behalf.

    ★ It lives here, beside the parser, because two callers now read it and they
    point opposite ways. ★

    `done` uses it to decide **what to ask a person about**, where reading an
    unmarked criterion as the human's costs one extra question. `launch` uses it
    to decide **what an agent may tick unasked**, where the same reading is the
    only thing standing between an unclassified criterion and a box ticked by
    something that was never told it owned it. One predicate, one answer, and
    the conservative direction happens to be the same one in both -- which is
    exactly the property a second copy would eventually lose.
    """
    return ac_owner(text) != AC_AGENT


def ac_span(body: str) -> Tuple[int, int]:
    """The half-open range of line indexes ``ac_lines`` is allowed to read.

    ★ Whole line, never a substring, and never indented. ★

    Both halves are load-bearing and both were learned from real files in this
    workspace. Items *name* these markers inline -- one item's
    ``what_agent_can_do:`` field quotes both of them in a single frontmatter
    sentence, and three more do it in prose -- so a ``find()`` would open the
    span inside the frontmatter and read the rest of it as criteria. And an
    indented marker is somebody showing you a marker: indentation is what makes
    a code block, which is exactly the disguise the criteria themselves need
    protecting from. Trailing whitespace is allowed because it is invisible and
    an editor may add it; leading whitespace is meaning.

    **When a well-formed pair is absent, the range is the whole body** -- which
    is to say, precisely what this parser did before it had a range at all. That
    is a choice, and the alternative (report zero) was rejected for the reason
    written at the top of ``ac_lines``: a reader that does not recognise a
    criterion fails by *subtraction*, and subtraction has no symptom. ``AC 2/5``
    turning into ``AC 0/0`` does not read as a broken parser; it reads as an item
    that never promised anything, and the promise is gone with nothing to say it
    was made.

    Measured before choosing, on 2026-08-12: 51 of 51 items in the live
    workspace carry the pair, and 3 of 3 in ``examples/workspace/backlog/``, so
    nothing ``new`` writes ever takes this path and the narrowing costs nothing.
    But a body somebody *typed* is the normal shape for everything else --
    ``helpers.write_backlog_item`` writes its default without them, and an item
    older than the markers has no way to grow them. ``_needs_you`` settled the
    identical question in this file and reached the same answer: reading the
    absence of a marker as a claim "would empty the tick selector for the entire
    existing backlog in one move". Rule 6 of CONTRIBUTING points the same way.

    So this change can only ever narrow. Nothing counted today stops being
    counted, and the honest residue is that a marker-less body is still open to
    a criterion quoted in its prose -- for which the fix is markers.

    Half a pair is not a range. An ``AC:BEGIN`` with no ``AC:END`` falls back
    rather than running to the bottom of the file, because running to EOF would
    let one unclosed marker swallow NOTES whole -- this item's own bug, arriving
    through the repair for it. A pair that *is* well formed is an answer even
    when it is empty: falling back there would resurrect every checkbox in NOTES
    on exactly the items whose criteria have all been dropped.
    """
    begin = None
    lines = body.splitlines()
    for i, line in enumerate(lines):
        stripped = line.rstrip()
        if begin is None:
            if stripped == AC_BEGIN:
                begin = i
        elif stripped == AC_END:
            return begin + 1, i
    return 0, len(lines)


def ac_lines(body: str) -> List[Tuple[int, str, str]]:
    """``(line index, mark, text)`` for every acceptance criterion.

    ★ The one parser. Every other reader of criteria is a comprehension over
    this, and that is load-bearing rather than tidy. ★

    A mark only some of the readers know about does not fail loudly -- it fails
    by *subtraction*. Miss the counter and `AC 2/5` prints as `AC 2/4`, which
    does not read as a bug: it reads as an item that always had four criteria,
    and the promise that was set aside is gone with nothing to say it was ever
    made. Sharing the parser is what makes "all of them recognise it" a property
    of the code rather than a thing four functions have to remember.

    It lives here rather than in ``cli`` because ``sense`` reads it too, and
    ``sense`` may not import ``cli`` -- the dependency runs the other way. A
    second parser in the sensing stage would be the subtraction failure above
    with an extra copy to keep in step.

    ★ It reads ``ac_span``, not the whole body, and that is the second thing
    every reader inherits at once. ★

    A criterion is a line in a place, not a line anywhere. Scanning the whole
    body meant a sentence of NOTES *quoting* what a criterion looks like became
    one -- observed three times on 2026-08-12, the third time inside the bug
    report describing it, which is to say the report reproduced its own subject
    while being written. ``.strip()`` is why an indented code block was no
    shelter: by the time the shape was tested the indentation was gone.

    The cost was never the number. Per ``_unticked_acs``, an unticked criterion
    is drafted as ``future_work`` and ``followup`` mints that into a real
    backlog item -- so prose about criteria could become a task somebody is
    asked to do.

    The index returned is into ``body.splitlines()``, still, and that is not
    incidental: ``_apply_marks`` writes a tick at exactly that index. Re-indexing
    from the span would rewrite a line in the frontmatter instead, silently,
    because splicing characters 3..5 of the wrong line raises nothing.
    """
    out = []
    lines = body.splitlines()
    lo, hi = ac_span(body)
    for i in range(lo, hi):
        s = lines[i].strip()
        if s[:3] in ("- [", "* [") and len(s) > 5 and s[4] == "]":
            mark = s[3].lower()
            if mark in (AC_OPEN, AC_DONE, AC_DROPPED) and s[5:].strip():
                out.append((i, mark, s[5:].strip()))
    return out


# ---------------------------------------------------------------------------
# criteria that name nothing
# ---------------------------------------------------------------------------

# ★ Reads text. Runs nothing. ★
#
# Everything below decides whether a *sentence* names something, by looking at
# the characters in it. It never opens the file it thinks it sees, never runs
# the command it thinks it sees, and never asks whether the thing named exists.
# That is not a limitation to be fixed later -- it is the property that makes
# this safe to run over a backlog full of sentences somebody else wrote. The
# engine's oldest rule is that it does not execute what it reads, and a lint
# about criteria is the most tempting place to break it: "does `pytest -k foo`
# pass" is one `subprocess` call away from being answerable, and answering it
# would turn every item file in the workspace into a script.
#
# So the question is deliberately the weaker one: **is there anything here a
# person could go and look at afterwards?** A criterion that answers no is not
# waiting on anybody. It is broken, and it was broken the moment it was written.

# Fenced in backticks: a path, a symbol, a flag, a value. The strongest signal
# there is, because somebody deliberately marked it as a name.
_TRACE_CODE = re.compile(r"`[^`\n]+`")

# A quoted run with no whitespace in it -- `「还没有完成过的练习」`, the sentence a
# screen must stop saying. Whitespace is what separates a literal from prose:
# "does the tail get worse with tenant size" is a question somebody is quoting,
# not a string anybody can grep for, and counting it would let every rhetorical
# question in a backlog pass as evidence.
_TRACE_LITERAL = re.compile(r"[「『\"“]([^」』\"”\s]{5,})[」』\"”]")

# Identifier-shaped: `cli.py`, `--db`, `data/retention/`, `.gitignore`,
# `nightlySweep`, `VT_DB_PATH`, `demo-final-v3`, `NA-0034`, `2026-08-17`.
#
# Hyphens are the awkward case and get their own rule below, because
# `first-time` is an English word and `windows-latest` is a CI runner, and no
# amount of regex tells them apart.
#
# Two of these alternatives are narrower than they look, and both narrowings
# were put there by the nine English criteria in `examples/workspace/`:
#
#   * the separator must have something after it, or every English sentence
#     that ends in a full stop names `feed.` and nothing is ever flagged;
#   * a flag must start at a word boundary, or `first-time` is `-time`.
#
# Both passed silently on a CJK backlog, where the rule below this one was
# answering first. A lint that cannot fail is worth what a test that cannot
# fail is worth.
_TRACE_IDENT = re.compile(r"""
      [A-Za-z][A-Za-z0-9]*[_./\\:][A-Za-z0-9*][A-Za-z0-9_./\\:*+-]*  # cli.py
    | [A-Za-z][A-Za-z0-9_-]*/                             # assets/
    | \.[A-Za-z][A-Za-z0-9]{2,}                           # .gitignore
    | (?<![A-Za-z0-9])--?[A-Za-z][A-Za-z0-9-]+            # --db
    | [a-z][a-z0-9]*[A-Z][A-Za-z0-9]*                     # nightlySweep
    | [A-Z]{2,}[A-Z0-9_]*                                 # CONFIG, VT_DB_PATH
    | [A-Za-z]+[0-9][A-Za-z0-9]*                          # v3, md5, p95
    | \d{4}-\d{2}-\d{2}                                   # 2026-08-17
""", re.X)

# Hyphenated, and only counted when it cannot be one English word: three or
# more segments, or a digit somewhere in it.
_TRACE_HYPHEN = re.compile(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+){2,}"
                           r"|[A-Za-z]+-[A-Za-z0-9]*\d[A-Za-z0-9]*")

# Any CJK character. Its presence switches on the rule below it. Spelled in
# escapes rather than literals, so that a file re-encoded by an editor that
# means well cannot quietly turn this into a range matching nothing.
_CJK = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")

# A bare Latin word, counted ONLY in a criterion that is otherwise written in
# CJK -- where a Latin word is nearly always a name somebody reached for
# because there is no other name: `registry`, `tier`, `dormant`, `mode`,
# `assets`, `provider`, `skill`, `deck`, `domain`.
#
# ★ The asymmetry is deliberate, and it is the honest half of this whole file.
# ★ In an English criterion the same rule would match every word in the
# sentence, so it is off, and an English backlog is judged on the narrower
# evidence above plus the vocabulary below. The consequence is real and worth
# knowing before you trust a clean run: this was measured over 126 criteria in
# a CJK workspace and tried against nine English ones. Being quieter in English
# is the right direction to be wrong in -- see the note on the two error
# directions in `untraceable_acs`. The nine were not wasted: they are what
# caught the two regexes above matching `feed.` and `-time`.
_TRACE_WORD = re.compile(r"[A-Za-z][A-Za-z]{2,}")

# The format's own vocabulary, which every criterion carries and which
# therefore names nothing about any particular one.
_TRACE_STOP = frozenset(("agent", "you", "and", "the", "not", "for", "with"))

# Words that assert a *record* or a *check* rather than an act. The list is
# small on purpose: it is the last net, and every entry widened it. Chinese
# first, because that is what it was measured on.
_TRACE_WORDS = (
    "测试 判据 断言 回归 变异 命令 脚本 提交 分支 日志 报告 收据 快照 备份"
    " 文件 目录 路径 字段 标记 配置 参数 注释 文档 简报 表 图"
    " 留档 留痕 痕迹 记录 记进 记入 写进 写下 写回 写明 写成 注明 贴进 贴出"
    " 落在 落进 落到 定稿 产出 存在 不存在"
    " test assert guard regression mutation command script commit branch"
    " log report receipt snapshot backup file path directory field flag"
    " config comment docstring document checklist record note written write"
    " wrote page link appears exists timestamp"
    # Traces that are not files. The list above was measured on a Chinese
    # backlog of software work, and read as a general rule it flagged a signed
    # contract, a merged pull request and a bank refund -- things that outlive
    # the doing more durably than most of the words above it.
    " contract invoice bill payment refund order signature signed policy"
    " release tag tagged published merged review reviewed approved"
    # NOT `reply`, `message`, `email` or `thread`. Those name the SUBJECT of the
    # claim, not a record of it: "there was a reply" is the canonical criterion
    # this warning was built for, and it is unanswerable precisely because a
    # reply existing somewhere is not a place anybody can look. An artifact
    # (a contract, a screenshot, an export) is a thing; a reply is an event.
    " ticket issue screenshot recording export"
    " dashboard registration filed attached uploaded receipt entry"
).split()


def ac_trace(text: str) -> Optional[str]:
    """The first thing this criterion names that would outlive the doing.

    ``None`` when it names nothing -- which is the whole point of the function.

    Returns the token rather than a boolean so that a warning can say *why* it
    stayed quiet about a criterion somebody expected it to flag. Half the cost
    of a lint is arguing with it, and "it saw ``HOME``" ends that argument in
    one line where "it did not fire" starts a hunt through the rules.
    """
    body = _TRACE_CODE.sub(" ", text)
    for rx in (_TRACE_CODE, _TRACE_LITERAL):
        m = rx.search(text)
        if m:
            return m.group(0)
    for rx in (_TRACE_IDENT, _TRACE_HYPHEN):
        m = rx.search(body)
        if m:
            return m.group(0)
    if _CJK.search(body):
        for m in _TRACE_WORD.finditer(body):
            if m.group(0).lower() not in _TRACE_STOP:
                return m.group(0)
    # Whole words, not substrings. `word in lowered` read "bio-LOG-y" and
    # "apo-LOG-etic" as naming a log, which is not a smaller net but a
    # differently shaped one: it waves through the sentences that happen to
    # spell a trace word inside another word, and that correlates with nothing.
    # The Chinese half has no word boundaries to find, so it stays a substring
    # test -- which is correct for it and wrong for the Latin half.
    lowered = text.lower()
    for word in _TRACE_WORDS:
        if word.isascii():
            if re.search(r"\b%s\w{0,3}\b" % re.escape(word), lowered):
                return word
        elif word in lowered:
            return word
    return None


def _ac_paragraphs(body: str) -> List[Tuple[int, str, str]]:
    """``ac_lines``, with each criterion's wrapped continuation folded back in.

    A comprehension over ``ac_lines`` rather than a second scan, for the reason
    written at the top of it: a criterion this disagreed with about where it
    starts would be a criterion two features count differently.

    Wrapping is not decoration. Ten of the 142 criteria measured on
    2026-08-18 continue onto a second line, and four of those put the *only*
    checkable half there -- ``两半都贴进 NOTES``, ``把这个 0 连同尝试过的命令一起
    贴进 NOTES``. Reading the checkbox line alone would flag them, and being
    wrong about a criterion that is written correctly is exactly the failure
    this lint cannot afford.
    """
    lines = body.splitlines()
    found = ac_lines(body)
    _lo, hi = ac_span(body)
    starts = [i for i, _m, _t in found]
    out = []
    for n, (i, mark, text) in enumerate(found):
        stop = starts[n + 1] if n + 1 < len(starts) else hi
        tail = []
        for j in range(i + 1, stop):
            # Indented, and it stops at the first line that is not. A
            # continuation is indented under its bullet -- that is what makes
            # it a continuation -- and taking everything up to the next
            # checkbox instead would, on a body with no AC markers, fold the
            # whole of NOTES into the last criterion. That failure is silent
            # and it points the safe way (a criterion swallowing prose always
            # finds a noun and is never flagged), which is exactly the kind
            # nobody finds later.
            if not lines[j][:1].isspace() or not lines[j].strip():
                break
            tail.append(lines[j].strip())
        out.append((i, mark, " ".join([text] + tail)))
    return out


def untraceable_acs(body: str) -> List[Tuple[int, str]]:
    """Open criteria that name nothing anybody could go and look at.

    ``(line index, text)``, in file order.

    ★ Why this is a warning and never an error. ★ It is a judgement about a
    sentence, made by a regex, and it is wrong about roughly one criterion in
    six. `check` exits 3 to tell a scheduler the brief is out of date and 1 to
    tell a person two files claim one id; neither is true here, and neither is
    fixed by re-running anything. Nothing downstream may key off this.

    ★ The two ways of being wrong do not cost the same. ★ A criterion wrongly
    flagged is a warning that fires on work somebody did correctly -- and a
    warning that does that is one people learn to scroll past, after which the
    one that matters goes past unread too. A criterion wrongly *passed* is
    merely a miss: the item was already broken and stays exactly as broken as
    it was. So every rule above leans towards silence, and four known misses
    were left in rather than tightened out.

    Measured on the workspace this was written for, 2026-08-18, 126 open
    criteria across 31 live items: 14 of them name nothing that outlives the
    doing, and this flags 11 -- 10 of the 14, plus one that does name a real
    artifact (``25 个改动已分类处理``) without saying where it lands. The four it
    misses all *mention*
    something durable without asserting anything about it (``从快照恢复到一台
    干净机器`` names a snapshot and records nothing), which is a distinction
    between a noun and a claim that no regex is going to draw.

    ★ Open criteria only. ★ A ticked one has already been settled and a
    dropped one was set aside on purpose; warning about either is a complaint
    about history, which is the same thing as noise.
    """
    return [(i, text) for i, mark, text in _ac_paragraphs(body)
            if mark == AC_OPEN and ac_trace(text) is None]


def ac_progress(body: str) -> Tuple[int, int, int]:
    """``(ticked, dropped, total)`` over the item's acceptance criteria.

    Checkbox lines in the body, the same definition ``launch`` copies into the
    session prompt. Nothing here writes a tick on its own: ticking is a human
    act, which is exactly why the count is worth showing -- the engine can see
    the number and cannot move it.

    Dropped criteria stay in ``total``. They were promised, and a denominator
    that quietly shrinks when one is set aside hides the promise along with it.
    They are reported separately rather than folded into ``ticked``, because
    "we did this" and "we stopped meaning to" are different answers and only one
    of them is an achievement.
    """
    marks = [m for _i, m, _t in ac_lines(body)]
    return marks.count(AC_DONE), marks.count(AC_DROPPED), len(marks)


def status_of(fm: Dict[str, Any]) -> str:
    return str(fm.get("status") or "open").strip().lower()


def defer_due(fm: Dict[str, Any]) -> Optional[dt.date]:
    """The date a deferred item returns, or ``None`` when it does not parse."""
    raw = fm.get("deferred_until")
    if raw is None:
        return None
    try:
        return dt.date.fromisoformat(str(raw).strip()[:10])
    except (TypeError, ValueError):
        return None


def is_live(fm: Dict[str, Any], today: dt.date) -> bool:
    """Whether this item belongs on today's page.

    Nothing is written to bring a deferred item back. The status stays
    ``deferred`` and this function decides, from the date, whether it counts as
    open today -- so the return is a property of the file rather than of some run
    having happened on the right morning. A workspace nobody senses for a week
    still shows every item that came due during it.

    A deferred item whose date is missing or unreadable is **live**. That is the
    fail-safe direction and the only one available: the alternative is an item
    parked forever by a typo, which is exactly the silent abandonment ``defer``
    exists to prevent.
    """
    status = status_of(fm)
    if status in OPEN_STATUSES:
        return True
    if status != DEFERRED:
        return False
    due = defer_due(fm)
    return due is None or due <= today


def is_parked(fm: Dict[str, Any], today: dt.date) -> bool:
    """Deferred, and not due yet -- the only state in which an item is hidden."""
    return status_of(fm) == DEFERRED and not is_live(fm, today)


def days_until_due(fm: Dict[str, Any], today: dt.date) -> Optional[int]:
    due = defer_due(fm)
    return None if due is None else (due - today).days


# ---------------------------------------------------------------------------
# the claim record
# ---------------------------------------------------------------------------

# ★ A record, not a lock. ★
#
# `in_progress` sat in OPEN_STATUSES and in the schema from the beginning, and
# nothing in the engine had ever written it: a state with a reader and no
# producer. `do` is the producer, and what it writes is the answer to "has
# somebody started on this, and where" -- a question that had no answer anywhere
# in the workspace, which is why the same item could be opened twice and why a
# session that went idle carrying the work was found two days later by reading
# transcripts.
#
# It is deliberately not a lock. The failure actually observed three times was
# **abandonment**, not collision, and the punishment a lock hands out for
# abandonment is to seal the item shut -- after which you need expiry, a clock,
# and a policy, which is a distributed lock manager living inside a single-user
# CLI. So a claim is shown and never enforced: `do` prints the one already there
# and asks, and every path through it ends with the work being possible.
IN_PROGRESS = "in_progress"
CLAIM = "claim"

# `where` is the directory the session was opened in and `branch` is the branch
# that directory was on, rather than one field holding "a branch or a worktree".
# `check` has to ask git a question about the branch, and git cannot be asked
# anything without a directory to ask it in -- a single polymorphic field would
# have to be guessed at by the one reader that exists, and guessing which of two
# things a field holds is how the id collision this came from behaved.
CLAIM_KEYS = ("by", "at", "where", "branch")


def claim_of(fm: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """The claim block, or ``None`` when the item carries none.

    A block with nothing in it is ``None`` as well: ``claim:`` with no subkeys
    parses to ``{}``, and an empty mapping is not a record of anybody.
    """
    claim = fm.get(CLAIM)
    if not isinstance(claim, dict) or not claim:
        return None
    return claim


def claim_lines(text: str) -> List[str]:
    """The claim block's own lines, verbatim, out of a whole item file.

    Verbatim because the one thing `do` owes a second caller is *what is already
    written down*, and a re-rendering of parsed values is a paraphrase -- it
    drops a hand-edited comment, normalises a date somebody typed differently,
    and quietly hides the very oddity that would tell you this claim is not what
    you think it is.
    """
    fm_end = text.find("\n---", 3)
    if not text.startswith("---") or fm_end == -1:
        return []
    out: List[str] = []
    inside = False
    for line in text[text.find("\n") + 1:fm_end].split("\n"):
        if inside:
            if line.startswith((" ", "\t")) or not line.strip():
                out.append(line)
                continue
            break
        # Column zero, so a nested key that happens to be called `claim` inside
        # some other block is not mistaken for the item's own claim.
        if line.startswith("%s:" % CLAIM):
            inside = True
            out.append(line)
    while out and not out[-1].strip():
        out.pop()
    return out


def claim_age_days(claim: Dict[str, Any], today: dt.date) -> Optional[int]:
    """Days since ``claim.at``. ``None`` when the date is missing or unreadable.

    ``None`` rather than zero, so a caller deciding whether a claim has gone
    quiet cannot get that answer from a date nobody wrote.
    """
    try:
        return (today - dt.date.fromisoformat(str(claim.get("at")).strip()[:10])).days
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# the closing record
# ---------------------------------------------------------------------------

CLOSING_BEGIN = "<!-- SECTION:CLOSING:BEGIN -->"
CLOSING_END = "<!-- SECTION:CLOSING:END -->"

# `- some follow-up -> NA-0023`. Anchored at the end of the line so a sentence
# containing an arrow is not mistaken for a promotion.
_PROMOTED = re.compile(r"\s+->\s+([A-Za-z][A-Za-z0-9]*-\d+)\s*$")
_ID = re.compile(r"^([A-Za-z][A-Za-z0-9]*)-(\d+)$")


class FutureWork(NamedTuple):
    text: str
    promoted_to: Optional[str]


# Who wrote the summary. `""` is a fourth, unwritable value: a record closed
# before this field existed, which is not the same claim as `none`.
SUMMARY_HUMAN = "human"
SUMMARY_DRAFT = "accepted_draft"
SUMMARY_NONE = "none"


class Closing(NamedTuple):
    closed_on: str
    summary: str
    future_work: List[FutureWork]
    # ★ Whose sentence this is. ★
    #
    # Everything else in this tool records provenance -- `created_by`,
    # `human_confirmed`, the evidence gate -- and the closing summary is the one
    # place where a machine-derived sentence could end up filed under a person's
    # name. A draft the engine offered and a person accepted is a real answer,
    # but it is not testimony, and six months later the difference is the whole
    # reason to trust or distrust the line.
    summary_source: str = ""

    @property
    def empty(self) -> bool:
        return not self.summary.strip() and not self.future_work


def _block(text: str) -> Optional[Tuple[int, int, str]]:
    """``(start, end, inner)`` of the closing block, or None."""
    start = text.find(CLOSING_BEGIN)
    if start == -1:
        return None
    end = text.find(CLOSING_END, start)
    if end == -1:
        return None
    return start, end + len(CLOSING_END), text[start + len(CLOSING_BEGIN):end]


def parse_closing(text: str) -> Optional[Closing]:
    """Read the closing record out of a whole item file. ``None`` when absent.

    Never raises. A record somebody hand-edited into something this cannot read
    yields empty fields rather than an exception -- the file is prose a person
    owns, and the summary view is not worth a crashed command.
    """
    found = _block(text)
    if found is None:
        return None
    _s, _e, inner = found

    closed_on = ""
    source = ""
    summary_lines: List[str] = []
    future: List[FutureWork] = []
    mode = None
    for line in inner.split("\n"):
        stripped = line.strip()
        if mode == "summary":
            # Any unindented, non-blank line ends the block scalar.
            if not stripped or line.startswith("  "):
                summary_lines.append(line[2:] if line.startswith("  ") else "")
                continue
            mode = None
        if stripped.startswith("closed_on:"):
            closed_on = stripped[len("closed_on:"):].strip()
            continue
        # Before `summary:`, because `startswith("summary:")` matches both.
        if stripped.startswith("summary_source:"):
            source = stripped[len("summary_source:"):].strip()
            continue
        if stripped == "summary: |":
            mode, summary_lines = "summary", []
            continue
        if stripped.startswith("summary:"):
            summary_lines = [stripped[len("summary:"):].strip()]
            continue
        if stripped == "future_work:":
            mode = "future"
            continue
        if mode == "future" and stripped.startswith("- "):
            body = stripped[2:].strip()
            m = _PROMOTED.search(body)
            future.append(FutureWork(_PROMOTED.sub("", body).strip(),
                                     m.group(1) if m else None))
    return Closing(closed_on, "\n".join(summary_lines).strip(), future, source)


def render_closing(closing: Closing) -> str:
    """The block, markers included, ready to drop into a file.

    Keys are English and structural, like the frontmatter's. They are read by
    ``nextbrief closed`` in every locale, so a translated heading here would be a
    view that works in one language and silently finds nothing in the other.
    """
    lines = [CLOSING_BEGIN, "closed_on: %s" % (closing.closed_on or "")]
    # Only when it is known. Writing `summary_source:` onto a record closed
    # before the field existed would invent a provenance nobody recorded.
    if closing.summary_source:
        lines.append("summary_source: %s" % closing.summary_source)
    summary = closing.summary.strip()
    if summary:
        lines.append("")
        lines.append("summary: |")
        lines.extend(("  " + ln).rstrip() for ln in summary.split("\n"))
    if closing.future_work:
        lines.append("")
        lines.append("future_work:")
        for entry in closing.future_work:
            tail = " -> %s" % entry.promoted_to if entry.promoted_to else ""
            lines.append("- %s%s" % (entry.text.strip(), tail))
    lines.append(CLOSING_END)
    return "\n".join(lines)


def upsert_closing(text: str, closing: Closing) -> str:
    """Put ``closing`` into an item file, replacing any block already there.

    Appended at the end rather than woven into the body: everything above it was
    written while the item was open, and a record of how it ended reads as a
    postscript because that is what it is.
    """
    rendered = render_closing(closing)
    found = _block(text)
    if found is not None:
        start, end, _inner = found
        return text[:start] + rendered + text[end:]
    body = text.rstrip("\n")
    return "%s\n\n%s\n" % (body, rendered) if body else rendered + "\n"


def record_promotion(text: str, index: int, new_id: str) -> str:
    """Note in the file that future-work entry ``index`` became ``new_id``.

    The edge is written on both sides -- ``discovered_from`` on the new item, the
    id here -- because each answers a question the other cannot. From the new
    item: where did this come from. From here: was this follow-up ever picked up,
    or is it still a sentence nobody acted on.
    """
    closing = parse_closing(text)
    if closing is None or not (0 <= index < len(closing.future_work)):
        return text
    entries = list(closing.future_work)
    entries[index] = FutureWork(entries[index].text, new_id)
    return upsert_closing(text, closing._replace(future_work=entries))


# ---------------------------------------------------------------------------
# minting a new item
# ---------------------------------------------------------------------------


def next_item_id(existing: Sequence[str], like: str) -> str:
    """The next free id sharing ``like``'s prefix and zero padding.

    Derived from an id that exists rather than from a constant: the prefix is a
    workspace's own convention (``NA-``, ``P-``, anything), and hard-coding one
    would mint follow-ups into a namespace the rest of the backlog does not use.
    """
    m = _ID.match(str(like).strip())
    prefix, width = (m.group(1), len(m.group(2))) if m else ("NA", 4)
    highest = 0
    for item_id in existing:
        got = _ID.match(str(item_id).strip())
        if got and got.group(1) == prefix:
            highest = max(highest, int(got.group(2)))
    return "%s-%0*d" % (prefix, width, highest + 1)


def id_shape(existing: Sequence[str]) -> str:
    """An id worth modelling the next one on, for a caller with no item in hand.

    ``next_item_id`` takes the prefix and the zero padding from an id somebody
    already chose, which ``followup`` has (the item being closed) and a bare
    ``new`` does not. The backlog's own habit is the next best evidence: the
    prefix most of it already uses, at the widest padding seen with that prefix.

    Widest rather than commonest padding, because the two disagree exactly when
    a backlog has grown past its first numbering -- ``NA-001`` alongside
    ``NA-0044`` -- and narrowing is the direction that collides. ``NA-0001``
    when there is nothing to go on, because a first item still has to be
    called something.
    """
    counts: Dict[str, int] = {}
    widths: Dict[str, int] = {}
    for item_id in existing:
        got = _ID.match(str(item_id).strip())
        if not got:
            continue
        prefix, digits = got.group(1), got.group(2)
        counts[prefix] = counts.get(prefix, 0) + 1
        widths[prefix] = max(widths.get(prefix, 0), len(digits))
    if not counts:
        return "NA-0001"
    # Ties broken alphabetically, so two prefixes used equally often do not make
    # the answer depend on the order the directory happened to be read in.
    prefix = sorted(counts, key=lambda p: (-counts[p], p))[0]
    return "%s-%0*d" % (prefix, widths[prefix], 1)


def slug(title: str, limit: int = 48) -> str:
    """A filename fragment. Keeps letters and digits in any script -- a CJK
    backlog would otherwise produce files named after nothing but their id."""
    out = []
    for ch in str(title).strip().lower():
        if ch.isalnum():
            out.append(ch)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-")[:limit].strip("-") or "item"


def _item_text(item_id: str, title: str, project: str, today: str,
               source_doc: Optional[str], anchor: Optional[str],
               discovered_from: Optional[str], note: str) -> str:
    """The one shape a newly minted backlog file has.

    Both minting paths go through it. Spelled twice, the two would drift the
    first time a field was added -- and the drift would be invisible, because
    each path produces a file that reads fine on its own and only the sensing
    stage would notice that half the backlog is missing a key.

    ``human_confirmed: true`` and ``created_by: human``, and both are literally
    true on both paths: a person typed the sentence and typed the command that
    wrote it down. Automatic decay only ever withdraws the agent's own
    unconfirmed guesses, and this is neither.
    """
    lines = [
        "---",
        "id: %s" % item_id,
        "title: %s" % title,
        "project: %s" % project,
        "type: task",
        "status: open",
        "priority: 2",
        "blocked_by: none",
        "is_next_action: false",
        "automation:",
        "  tier: explore",
        "  what_agent_can_do: not assessed yet",
        "  what_needs_human: not assessed yet",
        "  next_probe: not assessed yet",
        "  assessed_on: %s" % today,
        "  human_confirmed: false",
        "source:",
        "  doc: %s" % (source_doc or "null"),
        "  anchor: %s" % (anchor or "null"),
        "  seen_on: %s" % today,
        "estimate_min: 30",
        "dependencies: []",
        "discovered_from: %s" % (discovered_from or "null"),
        "created_date: %s" % today,
        "updated_date: %s" % today,
        "created_by: human",
        "human_confirmed: true",
        "---",
        "",
        "<!-- SECTION:NEXT_ACTION:BEGIN -->",
        title,
        "<!-- SECTION:NEXT_ACTION:END -->",
        "",
        "<!-- AC:BEGIN -->",
        "- [ ] #1 %s" % title,
        "<!-- AC:END -->",
        "",
        "<!-- SECTION:NOTES:BEGIN -->",
        note,
        "<!-- SECTION:NOTES:END -->",
        "",
    ]
    return "\n".join(lines)


def new_item_text(item_id: str, title: str, project: str, discovered_from: str,
                  today: str, source_note: str = "") -> str:
    """A backlog file for a follow-up lifted out of a closing record."""
    return _item_text(
        item_id, title, project, today,
        source_doc="backlog/%s" % (source_note or discovered_from),
        anchor="closing record of %s" % discovered_from,
        discovered_from=discovered_from,
        note="Lifted out of the closing record of %s on %s. Nobody has sized it, "
             "scoped it, or decided it is worth doing -- it is here so that it "
             "stopped being something only one person remembered."
             % (discovered_from, today),
    )


def blank_item_text(item_id: str, title: str, project: str, today: str) -> str:
    """A backlog file for something a person decided to track, from nothing.

    No ``source`` and no ``discovered_from``: this item came out of somebody's
    head rather than out of a document, and inventing a provenance for it would
    put a citation in the one field the whole tool treats as evidence. ``null``
    is the honest value and the schema's own -- an item with no antecedent
    already writes it.
    """
    return _item_text(
        item_id, title, project, today,
        source_doc=None, anchor=None, discovered_from=None,
        note="Opened by hand on %s. Nothing here has been sized, scoped or "
             "confirmed as worth doing -- it is written down so that it stopped "
             "being something only one person remembered." % today,
    )


NOTES_END = "<!-- SECTION:NOTES:END -->"


def append_note(text: str, line: str) -> str:
    """Add one line to the end of the NOTES block, leaving the rest alone.

    Appends rather than prepends because NOTES is a log: the order records when
    each thing was learned, and a newest-first block loses that the moment two
    entries stop being obviously dated.

    A file with no NOTES marker gets the line at the end rather than a
    synthesised block. Inventing structure in somebody's file is how a tool
    starts owning a document it was only supposed to add a line to.
    """
    if not line:
        return text
    block = "\n" + line.rstrip() + "\n"
    if NOTES_END not in text:
        return text.rstrip("\n") + "\n" + block
    head, _sep, tail = text.rpartition(NOTES_END)
    return head.rstrip("\n") + block + NOTES_END + tail
