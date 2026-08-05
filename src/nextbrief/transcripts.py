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


def iter_timestamps(path: Path) -> Iterator[dt.datetime]:
    """Every top-level record timestamp in one transcript, as local datetimes.

    Streams. A transcript runs to tens of megabytes and there is no reason to
    hold one in memory to find its dates.
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
            if when is not None:
                yield when


def read_activity(path: Path) -> Tuple[Optional[dt.datetime], Dict[str, str], int]:
    """(last activity, the set of local days touched, records read).

    The day set is the point. A transcript that ran past midnight touched two
    days, and no single timestamp -- mtime, first record or last -- can say so.

    Days are returned as a dict keyed by ISO date so a caller can union several
    transcripts without re-deriving the strings.
    """
    last: Optional[dt.datetime] = None
    days: Dict[str, str] = {}
    count = 0
    for when in iter_timestamps(path):
        count += 1
        days[when.date().isoformat()] = ""
        if last is None or when > last:
            last = when
    return last, days, count
