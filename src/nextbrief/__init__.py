"""nextbrief -- what happened across your projects, and what you do next.

Three stages, and the order matters:

    sense    deterministic   filesystem + git  ->  snapshot.json
    interpret    a model     snapshot          ->  brief.json
    render   deterministic   brief + snapshot  ->  BRIEF.md / BRIEF.html

The third stage is the point. Every claim the model makes carries an ``evidence``
reference; the renderer resolves each one against the snapshot and drops what it
cannot verify into ``log/rejected.jsonl``. "Do not invent progress" stops being a
line in a prompt that a model may drift from, and becomes a property of the
pipeline that drift cannot defeat.
"""

__version__ = "0.1.0rc11"
__all__ = ["__version__"]
