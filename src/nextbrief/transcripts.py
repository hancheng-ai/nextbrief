"""The only module that opens an agent transcript.

Everything that reads inside a session log lives here, so that the rules about
what may escape one are stated and enforced in a single place rather than
re-derived at each call site.

**What a transcript contains.** The user's prompts, file contents, tool output,
and absolute paths on the machine that produced it. `snapshot.json` feeds
`digest.json` feeds the model feeds `BRIEF.md`, which is git-tracked. That is a
one-way path, so this module emits a strict **allowlist** of shapes -- ISO dates,
counts, and nothing that was typed or quoted. No function here returns a line, a
message body, or a path read out of a transcript.

**Why it parses at all**, when the sensor's own comment used to say "stat only,
never parsed". Because the mtime was wrong, measurably and in one direction:
across the local store, 96% of transcripts end on a record carrying no timestamp
at all -- a title rewrite, a mode change, a file-history snapshot -- so the mtime
was timing metadata churn rather than conversation. Measured against a full
parse, mtime invented 8 session-days that never happened and missed 27 that did.
A second cause is structural and cannot be fixed by any timestamp: a third of
transcripts span more than one day, and one mtime can only ever mark one.

**Cost.** A byte prefilter rejects the ~19% of lines that carry no timestamp
before any JSON is built, which is where nearly all the time goes. Measured on
the local store: 1.36 s for the whole tier.

**Timezone.** Every timestamp in a transcript is ISO-8601 with a literal trailing
``Z`` -- UTC. Every date elsewhere in this package is naive local, including
``as_of``. So this module converts once, here, and hands back local dates. Doing
it anywhere else would mix the two, and a day boundary is exactly where that
shows up.
"""

from __future__ import annotations

import calendar
import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, Tuple

# Only lines that could carry the field are parsed. Cheap, and correct in the
# only direction that matters: a line without the bytes cannot have the key, so
# the filter can never hide a record that JSON parsing would have found.
_TS_BYTES = b'"timestamp"'

# ISO-8601 with a trailing Z, optional fractional seconds.
#
# Hand-parsed rather than handed to ``datetime.fromisoformat``, which on the 3.9
# floor raises on the trailing ``Z`` and accepts it from 3.11. Every timestamp in
# the store carries that Z, so ``fromisoformat`` here would parse nothing at all
# on the oldest interpreter this package supports -- and would do it silently,
# returning a project with no sessions rather than an error.
_ISO_Z = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?Z$"
)


def parse_utc_to_local(value: Any) -> Optional[dt.datetime]:
    """A UTC transcript timestamp as a naive **local** datetime.

    Returns ``None`` for anything unparseable rather than raising: a single
    malformed record must not cost the whole file, and the caller counts what it
    could not read instead of stopping.
    """
    if not isinstance(value, str):
        return None
    m = _ISO_Z.match(value.strip())
    if not m:
        return None
    year, mon, day, hour, minute, sec = (int(m.group(i)) for i in range(1, 7))
    try:
        epoch = calendar.timegm((year, mon, day, hour, minute, sec, 0, 0, 0))
        return dt.datetime.fromtimestamp(epoch)
    except (ValueError, OverflowError, OSError):
        return None


class ScanStats:
    """Proportions that collapse when a parser silently breaks for a subset.

    A binary check cannot see partial breakage. "Did the session sensor run?"
    answers yes when it read 44 transcripts and understood 6 of them, and that is
    not a hypothetical -- it is the shape of every silent failure this format has
    produced. A ratio answers the same question with a number that moves.

    Each of these has a measured baseline of essentially 1.0 on a healthy store,
    which is what makes a collapse unmistakable rather than a matter of judgement:

    * ``envelope_coverage`` -- assistant records carrying a complete accounting
      envelope. Measured 53,085 of 53,085. A format change that renames a field
      takes this to zero while everything else still reports success.
    * ``attribution_rate`` -- dated records that landed in a registered project.
      Falls when the registry stops describing where the work happens, which is a
      real state and not a bug, so this one is information rather than an alarm.
    * ``dedup_ratio`` -- messages charged per usage-bearing record. Around 0.36
      on a healthy store because one response is written across ~2.77 records.
      **This one collapses upward.** If the dedup key breaks it climbs toward
      1.0, and every token figure silently inflates by up to 2.7x.

    A rate whose denominator is zero is ``None``, never 1.0. Nothing measured and
    everything correct must not look identical -- that substitution is the
    failure these exist to catch, and writing it into the sentinel itself would
    be the joke telling itself.
    """

    def __init__(self) -> None:
        self.transcripts_read = 0
        self.records_dated = 0
        self.records_attributed = 0
        self.assistant_records = 0
        self.usage_records = 0

    @staticmethod
    def _rate(numerator: int, denominator: int) -> Optional[float]:
        if denominator <= 0:
            return None
        # Three places is enough to see a collapse and few enough that a
        # rounding wobble does not read as a change worth investigating.
        return round(numerator / denominator, 3)

    def as_dict(self, messages_charged: int) -> Dict[str, Any]:
        return {
            "transcripts_read": self.transcripts_read,
            "records_dated": self.records_dated,
            "envelope_coverage": self._rate(self.usage_records, self.assistant_records),
            "attribution_rate": self._rate(self.records_attributed, self.records_dated),
            "dedup_ratio": self._rate(messages_charged, self.usage_records),
        }


def _usage_of(rec: Dict[str, Any]) -> Tuple[Optional[str], int, int]:
    """(message id, input tokens, output tokens) for one record.

    Input is the sum of the fresh, cache-written and cache-read counts. Splitting
    them would invite a "cheaper" reading of a cache hit, and this module does not
    price anything -- see `TokenLedger`.
    """
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return None, 0, 0
    mid = msg.get("id")
    usage = msg.get("usage")
    if not isinstance(mid, str) or not isinstance(usage, dict):
        return None, 0, 0

    def count(key: str) -> int:
        got = usage.get(key)
        # Negative or non-integer is a shape this format does not produce; if it
        # ever does, a zero is a wrong number that stays obviously wrong rather
        # than a negative that quietly cancels a real one out.
        return got if isinstance(got, int) and got >= 0 else 0

    return (mid,
            count("input_tokens") + count("cache_creation_input_tokens")
            + count("cache_read_input_tokens"),
            count("output_tokens"))


class TokenLedger:
    """Charge each message exactly once, however many records carry it.

    **Tokens, never money.** No price, no rate, no currency, and no field that
    could carry one. A token count is a fact the transcript states; a cost is a
    guess about a price list that changes without telling anyone, and a wrong
    number about somebody's money is worse than no number.

    **Why the key is `message.id` alone**, and not the `(message.id, requestId)`
    pair this was specified as. Measured over a real store there are 19,224
    distinct message ids against 19,183 request ids: the pair is *finer* than the
    message, so keying on it splits messages that should be charged once and
    leaves part of the overcount in place. Request id is a transport detail;
    the message is the thing that was generated.

    Two independent duplication paths make naive summing wrong, and only a
    ledger spanning every file catches both:

    1. One API response is written one record per content block -- 2.77 assistant
       records per message id -- with the usage figures replicated identically
       across them. Summing records multiplies by that factor.
    2. Resuming a session replays earlier records into a new file, so 26.7% of
       requests appear in more than one transcript. A per-file ledger would
       charge each of them again.

    Together they overcount by 2.697x, which is not a rounding error; it is a
    number that would make every proportion in the brief wrong.

    **Max-wins on a repeat.** Replicas of one message normally agree exactly. When
    they do not, the larger figure is the one that was not truncated: a partially
    written record carries a short count, never a long one.
    """

    def __init__(self) -> None:
        # message id -> [bucket, ISO day, input, output]
        self._charges: Dict[str, list] = {}
        self.records_seen = 0
        self.repeats = 0

    def charge(self, rec: Dict[str, Any], bucket: str, when: dt.datetime) -> None:
        mid, inp, out = _usage_of(rec)
        if mid is None:
            return
        self.records_seen += 1
        prior = self._charges.get(mid)
        if prior is None:
            self._charges[mid] = [bucket, when.date().isoformat(), inp, out]
            return
        # Seen before, in this file or another. Keep the first sighting's project
        # and day -- traversal is sorted, so "first" is the same on every run --
        # and take the larger counts.
        self.repeats += 1
        prior[2] = max(prior[2], inp)
        prior[3] = max(prior[3], out)

    @property
    def messages_charged(self) -> int:
        return len(self._charges)

    def totals(self) -> Iterator[Tuple[str, str, int, int]]:
        """(bucket, ISO day, input, output), one row per charged message."""
        for _mid, (bucket, day, inp, out) in sorted(self._charges.items()):
            yield bucket, day, inp, out


def iter_records(path: Path) -> Iterator[Tuple[dt.datetime, Optional[str], Dict[str, Any]]]:
    """Every datable record in one transcript, as (local time, cwd).

    Streams. A transcript runs to tens of megabytes and there is no reason to
    hold one in memory to find its dates.

    The prefilter is on the timestamp key rather than on ``cwd``: a record
    carrying a working directory but no time cannot place work on a day, so it is
    nothing this module can use. Filtering on the field we cannot do without
    keeps one cheap test in the hot loop instead of two.
    """
    with open(path, "rb") as fh:
        for raw in fh:
            if _TS_BYTES not in raw:
                continue
            try:
                rec = json.loads(raw.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                # A torn final line is normal: the file may be appended to while
                # this runs. Skipping it loses one record, never the file.
                continue
            if not isinstance(rec, dict):
                continue
            when = parse_utc_to_local(rec.get("timestamp"))
            if when is None:
                continue
            cwd = rec.get("cwd")
            yield when, (cwd if isinstance(cwd, str) else None), rec


def read_activity(path: Path, resolve,
                  ledger: Optional[TokenLedger] = None,
                  stats: Optional[ScanStats] = None
                  ) -> Tuple[Dict[str, Dict[str, Any]], int]:
    """({bucket: {"days": ..., "last": ...}}, records attributed to nothing).

    Two things are load-bearing here.

    **A transcript is a sequence of directories, not one.** The working directory
    is recorded per record and changes mid-session -- in a real store, a third of
    transcripts record more than one and one records seventeen. Attributing the
    whole file to where it started throws that away: measured, per-record
    attribution finds 82 project-days where the launch directory finds 57, adds
    four projects that had no sessions at all, and loses nothing.

    **The caller passes `resolve`, and no working directory is ever returned.**
    A cwd is an absolute path on somebody's machine, and everything this module
    produces flows to `digest.json`, then to a model, then onto a git-tracked
    page. So the mapping from directory to bucket happens behind this call and
    only the bucket -- a project id, or a name for "somewhere else" -- comes back.
    The allowlist is enforced by the shape of the function rather than by
    remembering to strip a field.

    ``resolve(cwd) -> bucket`` may return ``None`` to discard a record entirely.

    Last activity is tracked **per bucket**, which is the only defensible reading
    once one transcript can touch several projects. A session that worked in one
    project all morning and moved to another after lunch did not leave the first
    one active until midnight; taking the file's final timestamp for every bucket
    it touched would overstate the recency of everything it passed through, and
    recency is what decides hot/warm/cold and what gets called neglected.
    """
    buckets: Dict[str, Dict[str, Any]] = {}
    unattributed = 0
    if stats is not None:
        stats.transcripts_read += 1
    for when, cwd, rec in iter_records(path):
        bucket = resolve(cwd)
        if stats is not None:
            # Counted before the placement check, and the assistant tallies
            # before the token one, so that a record which fails to place still
            # registers in the denominators. Counting only what survived would
            # give every rate a value of 1.0 by construction.
            stats.records_dated += 1
            if bucket is not None:
                stats.records_attributed += 1
            if rec.get("type") == "assistant":
                stats.assistant_records += 1
                if _usage_of(rec)[0] is not None:
                    stats.usage_records += 1
        if bucket is None:
            unattributed += 1
            continue
        if ledger is not None:
            # Charged before the day bookkeeping, and against the same bucket, so
            # a token can never be attributed to a project the record was not.
            ledger.charge(rec, bucket, when)
        acc = buckets.setdefault(bucket, {"days": {}, "last": None})
        acc["days"][when.date().isoformat()] = ""
        if acc["last"] is None or when > acc["last"]:
            acc["last"] = when
    return buckets, unattributed
