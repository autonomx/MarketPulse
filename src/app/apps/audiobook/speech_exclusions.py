"""Source-bound, reversible omissions from spoken text."""
from __future__ import annotations

from typing import Any


def load_speech_exclusions(connection: Any, context: Any, project_id: str, chapter_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT e.id, e.start_offset, e.end_offset, e.source_text
             FROM omnix_audiobook_speech_exclusions e
             JOIN omnix_audiobook_chapters c
               ON c.workspace_id = e.workspace_id AND c.canonical_hash = e.chapter_hash
              AND c.ordinal = e.chapter_ordinal
             JOIN omnix_audiobook_projects p
               ON p.workspace_id = c.workspace_id AND p.id = e.project_id
              AND p.current_source_revision_id = c.source_revision_id
            WHERE e.workspace_id = %s AND e.project_id = %s AND c.id = %s
              AND e.active AND p.deleted_at IS NULL
            ORDER BY e.start_offset, e.end_offset""",
        (context.workspace_id, project_id, chapter_id),
    ).fetchall()
    return [{"id": str(row[0]), "start_offset": int(row[1]), "end_offset": int(row[2]), "source_text": str(row[3])} for row in rows]


def exclusions_for_span(exclusions: list[dict[str, Any]], start: int, end: int) -> list[dict[str, Any]]:
    return [{**item, "start_offset": max(start, item["start_offset"]) - start,
             "end_offset": min(end, item["end_offset"]) - start}
            for item in exclusions if item["start_offset"] < end and item["end_offset"] > start]


def chapter_removed_sql(chapter: str, project_id: str) -> str:
    """SQL condition: the chapter aliased ``chapter`` is removed from the audiobook.

    A removed chapter has one active exclusion over its whole canonical text. ``project_id``
    is the SQL expression (a column or a placeholder) naming the project. Both arguments are
    fixed identifiers written by callers, never request data.
    """
    return f"""EXISTS (
        SELECT 1 FROM omnix_audiobook_speech_exclusions AS removed
         WHERE removed.workspace_id = {chapter}.workspace_id AND removed.project_id = {project_id}
           AND removed.chapter_hash = {chapter}.canonical_hash AND removed.chapter_ordinal = {chapter}.ordinal
           AND removed.active AND removed.start_offset = 0
           AND removed.end_offset >= char_length({chapter}.canonical_text))"""


def load_removed_chapters(connection: Any, context: Any, project_id: str) -> dict[str, str]:
    """``{chapter_id: exclusion_id}`` for the current source's chapters removed from the audiobook."""
    rows = connection.execute(
        """SELECT c.id, e.id
             FROM omnix_audiobook_projects p
             JOIN omnix_audiobook_chapters c
               ON c.workspace_id = p.workspace_id AND c.source_revision_id = p.current_source_revision_id
             JOIN omnix_audiobook_speech_exclusions e
               ON e.workspace_id = c.workspace_id AND e.project_id = p.id
              AND e.chapter_hash = c.canonical_hash AND e.chapter_ordinal = c.ordinal
              AND e.active AND e.start_offset = 0 AND e.end_offset >= char_length(c.canonical_text)
            WHERE p.workspace_id = %s AND p.id = %s AND p.deleted_at IS NULL""",
        (context.workspace_id, project_id),
    ).fetchall()
    return {str(row[0]): str(row[1]) for row in rows}
