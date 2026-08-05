"""Corrections dropped by the page, folded back in on the next run.

The brief is a file on disk opened over ``file://``. It has no server, no
session and no API, and it must keep working when the machine is offline and the
model is unpaid for. The one write-to-disk available under those conditions is
``<a download>`` of a Blob, which needs no permission and no network -- so a
control in the page writes a small JSON file into the browser's download
directory, and the next run reads whatever it finds.

That is the whole mechanism. What follows is the part that matters.

**Inline may CORRECT a claim the brief printed. It may never ORIGINATE a
judgement.** A control that appears next to a verdict is answering a question the
page already asked, in the context the page already supplied. A control that
appears next to anything else is a form -- and a form in a document nobody
opened to fill in is either ignored or answered carelessly, which is worse than
not asking. So exactly one field is adjustable, `status`, and only on a project
where the engine has stated an observation that contradicts the declared phase.
Everything else stays in `review`, where the whole portfolio is in one view,
because impact and positioning are *relative* judgements and cannot be made one
project at a time.

**A tab left open is a stale tab.** The payload carries `from_as_rendered`, the
`as_of` of the brief that drew the control. A correction is applied only if the
world still looks the way it did when the person clicked -- otherwise they are
answering a question about a state that no longer exists, and applying it would
put words in their mouth about facts they never saw.

**Nothing here trusts the file.** It arrives from a directory any process can
write to, so every field is checked, the project must exist, the value must be
one the question offers, and anything else is ignored and counted. A dropped file
is a suggestion from an unauthenticated source; the checks are what make it safe
to read one.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

__all__ = ["ADJUSTABLE_FIELDS", "DROP_GLOB", "read_adjustments", "apply_adjustments"]

# One field, and the reason is in the module docstring: it is the only one the
# page can put in front of somebody together with the observation that prompts
# it. Widening this list is a design change, not a configuration change.
ADJUSTABLE_FIELDS = ("status",)

# Named so a human scanning a downloads directory can tell what it is and delete
# it without worrying. The date is the brief's, not today's.
DROP_GLOB = "nextbrief-adjust-*.json"


def _valid_values(field: str, questions) -> Tuple[str, ...]:
    for q in questions:
        if q.field == field:
            return tuple(str(value) for value, _label in q.choices)
    return ()


def read_adjustments(drop_dir, questions, project_ids,
                     contradicted: Sequence[str],
                     as_of: Optional[dt.date] = None
                     ) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """(accepted corrections, counts of what was refused and why).

    The counts are returned rather than logged away because a correction that
    silently does nothing is the worst outcome available here: the person
    believes they have told the engine something, and the engine believes
    nothing happened. Every refusal has a name and gets surfaced.
    """
    refused = {"unreadable": 0, "stale": 0, "unknown_project": 0,
               "field_not_adjustable": 0, "value_not_offered": 0,
               "not_contradicted": 0}
    accepted: List[Dict[str, Any]] = []
    base = Path(drop_dir).expanduser()
    if not base.is_dir():
        return accepted, refused

    allowed = set(contradicted)
    known = set(project_ids)
    for path in sorted(base.glob(DROP_GLOB)):
        try:
            got = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeDecodeError):
            refused["unreadable"] += 1
            continue
        if not isinstance(got, dict):
            refused["unreadable"] += 1
            continue

        pid = got.get("project")
        field = got.get("field")
        value = got.get("value")
        rendered = got.get("from_as_rendered")

        if not isinstance(pid, str) or pid not in known:
            refused["unknown_project"] += 1
            continue
        if field not in ADJUSTABLE_FIELDS:
            refused["field_not_adjustable"] += 1
            continue
        if str(value) not in _valid_values(str(field), questions):
            refused["value_not_offered"] += 1
            continue
        # The staleness guard. A tab left open for three days is answering a
        # question about a state that has since changed, and applying it would
        # attribute to the person a statement about facts they never saw.
        if as_of is not None and str(rendered) != as_of.isoformat():
            refused["stale"] += 1
            continue
        # The never-originate rule, enforced here rather than trusted to the
        # page. The page decides which controls to DRAW; this decides which
        # corrections are legitimate, and a file on disk is not the page.
        if pid not in allowed:
            refused["not_contradicted"] += 1
            continue

        accepted.append({"project": pid, "field": str(field), "value": str(value),
                         "source": path.name})
    return accepted, refused


def apply_adjustments(accepted: Sequence[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Accepted corrections, shaped for `record_answers`.

    Later files win over earlier ones for the same project and field, which is
    the only reading that matches what a person did: they clicked twice because
    the second click is what they meant.
    """
    out: Dict[str, Dict[str, Any]] = {}
    for item in accepted:
        out.setdefault(item["project"], {})[item["field"]] = item["value"]
    return out
