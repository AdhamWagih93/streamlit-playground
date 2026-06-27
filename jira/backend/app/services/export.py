"""Export helpers: serialize issues and insights to downloadable formats.

Issues  -> CSV, JSON, XLSX
Insights -> JSON, CSV, Markdown

All functions return ``bytes`` ready to stream as a file response. Only the
caller's already-permission-filtered data should be passed in.
"""
from __future__ import annotations

import csv
import io
import json

from app.models import Issue
from app.schemas.analytics import OverviewStats, ProjectStats

# --- Issues ----------------------------------------------------------------
ISSUE_COLUMNS = [
    "Key", "Summary", "Project", "Type", "Status", "Category", "Priority",
    "Assignee", "Reporter", "Story Points", "Labels", "Components",
    "Fix Versions", "Sprint", "Due", "Resolution", "Created", "Updated",
]


def _issue_row(i: Issue) -> dict:
    def _iso(v):
        return v.isoformat() if v else ""

    return {
        "Key": i.key,
        "Summary": i.summary,
        "Project": i.project.key if i.project else "",
        "Type": i.type.name if i.type else "",
        "Status": i.status.name if i.status else "",
        "Category": i.status.category if i.status else "",
        "Priority": i.priority.name if i.priority else "",
        "Assignee": i.assignee.display_name if i.assignee else "",
        "Reporter": i.reporter.display_name if i.reporter else "",
        "Story Points": i.story_points if i.story_points is not None else "",
        "Labels": ", ".join(l.name for l in i.labels),
        "Components": ", ".join(c.name for c in i.components),
        "Fix Versions": ", ".join(v.name for v in i.fix_versions),
        "Sprint": i.sprint.name if i.sprint else "",
        "Due": _iso(i.due_date),
        "Resolution": i.resolution or "",
        "Created": _iso(i.created_at),
        "Updated": _iso(i.updated_at),
    }


def issues_csv(issues: list[Issue]) -> bytes:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=ISSUE_COLUMNS)
    writer.writeheader()
    for i in issues:
        writer.writerow(_issue_row(i))
    return buf.getvalue().encode("utf-8-sig")  # BOM => opens cleanly in Excel


def issues_json(issues: list[Issue]) -> bytes:
    return json.dumps([_issue_row(i) for i in issues], indent=2, default=str).encode("utf-8")


def issues_xlsx(issues: list[Issue]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Issues"
    ws.append(ISSUE_COLUMNS)
    for cell in ws[1]:
        cell.font = Font(bold=True)
    for i in issues:
        row = _issue_row(i)
        ws.append([row[c] for c in ISSUE_COLUMNS])
    ws.freeze_panes = "A2"
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


ISSUE_EXPORTERS = {"csv": issues_csv, "json": issues_json, "xlsx": issues_xlsx}


# --- Insights --------------------------------------------------------------
def insights_json(model: ProjectStats | OverviewStats) -> bytes:
    return model.model_dump_json(indent=2).encode("utf-8")


def _w(writer, *rows):
    for r in rows:
        writer.writerow(r)


def project_insights_csv(s: ProjectStats) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    _w(w,
       ["Project insights", s.project_key, s.project_name],
       ["Window", s.window.period, s.window.start or "", s.window.end or ""],
       [],
       ["Metric", "Value"],
       ["Total issues", s.total_issues], ["Open", s.open_issues],
       ["In progress", s.in_progress_issues], ["Closed", s.closed_issues],
       ["Resolution rate", round(s.resolution_rate, 3)],
       ["Avg velocity (points)", s.avg_velocity_points],
       ["Attention score (current)", s.attention_score],
       [],
       ["By status", "Count", "Category"])
    _w(w, *[[c.label, c.count, c.category] for c in s.by_status])
    _w(w, [], ["By type", "Count"], *[[c.label, c.count] for c in s.by_type])
    _w(w, [], ["By priority", "Count"], *[[c.label, c.count] for c in s.by_priority])
    _w(w, [], ["Velocity sprint", "Committed", "Completed", "Completed issues"],
       *[[v.sprint_name, v.committed_points, v.completed_points, v.completed_issues] for v in s.velocity])
    _w(w, [], ["Needs attention (current)", "Count", "Severity"],
       *[[a.label, a.count, a.severity] for a in s.attention])
    return buf.getvalue().encode("utf-8-sig")


def project_insights_md(s: ProjectStats) -> bytes:
    win = "all time" if s.window.period == "all" else f"{s.window.period} ({s.window.start} → {s.window.end})"
    lines = [
        f"# Insights — {s.project_name} ({s.project_key})",
        f"_Window: {win}_\n",
        "## Summary",
        "| Metric | Value |", "|---|---|",
        f"| Total issues | {s.total_issues} |",
        f"| Open | {s.open_issues} |",
        f"| In progress | {s.in_progress_issues} |",
        f"| Closed | {s.closed_issues} |",
        f"| Resolution rate | {round(s.resolution_rate * 100)}% |",
        f"| Avg velocity (points) | {s.avg_velocity_points} |",
        "",
        "## Needs attention (current state)",
    ]
    if s.attention:
        lines += ["| Signal | Count | Severity |", "|---|---|---|"]
        lines += [f"| {a.label} | {a.count} | {a.severity} |" for a in s.attention]
    else:
        lines.append("_Nothing needs attention right now._")
    lines += ["", "## By status", "| Status | Count |", "|---|---|"]
    lines += [f"| {c.label} | {c.count} |" for c in s.by_status]
    lines += ["", "## By type", "| Type | Count |", "|---|---|"]
    lines += [f"| {c.label} | {c.count} |" for c in s.by_type]
    if s.velocity:
        lines += ["", "## Velocity", "| Sprint | Committed | Completed |", "|---|---|---|"]
        lines += [f"| {v.sprint_name} | {v.committed_points} | {v.completed_points} |" for v in s.velocity]
    return ("\n".join(lines) + "\n").encode("utf-8")


def overview_csv(o: OverviewStats) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    _w(w,
       ["Instance insights", f"scope={o.scope}"],
       ["Window", o.window.period, o.window.start or "", o.window.end or ""],
       [],
       ["Metric", "Value"],
       ["Projects", o.total_projects], ["Total issues", o.total_issues],
       ["Open", o.open_issues], ["Closed", o.closed_issues],
       ["Resolution rate", round(o.resolution_rate, 3)],
       ["Projects needing attention", o.projects_needing_attention],
       ["Overdue (current)", o.total_overdue], ["High priority (current)", o.total_high_priority_open],
       ["Unassigned (current)", o.total_unassigned_open], ["Blocked (current)", o.total_blocked],
       ["Sprints at risk", o.projects_at_risk],
       [],
       ["Project", "Key", "Total", "Open", "Closed", "Resolution", "Attention score", "Overdue", "High priority", "Blocked", "At-risk sprint"])
    _w(w, *[[p.project_name, p.project_key, p.total_issues, p.open_issues, p.closed_issues,
             round(p.resolution_rate, 3), p.attention_score, p.overdue, p.high_priority_open,
             p.blocked, p.at_risk_sprint] for p in o.projects])
    return buf.getvalue().encode("utf-8-sig")


def overview_md(o: OverviewStats) -> bytes:
    win = "all time" if o.window.period == "all" else f"{o.window.period} ({o.window.start} → {o.window.end})"
    lines = [
        "# Instance insights",
        f"_Scope: {o.scope} · Window: {win}_\n",
        "## Summary",
        "| Metric | Value |", "|---|---|",
        f"| Projects | {o.total_projects} |",
        f"| Total issues | {o.total_issues} |",
        f"| Open | {o.open_issues} |",
        f"| Closed | {o.closed_issues} |",
        f"| Resolution rate | {round(o.resolution_rate * 100)}% |",
        f"| Projects needing attention | {o.projects_needing_attention} |",
        f"| Overdue (current) | {o.total_overdue} |",
        f"| Sprints at risk | {o.projects_at_risk} |",
        "",
        "## Projects (by attention)",
        "| Project | Total | Open | Closed | Attention | Overdue | High | Blocked |",
        "|---|---|---|---|---|---|---|---|",
    ]
    lines += [f"| {p.project_key} | {p.total_issues} | {p.open_issues} | {p.closed_issues} | "
              f"{p.attention_score} | {p.overdue} | {p.high_priority_open} | {p.blocked} |"
              for p in o.projects]
    return ("\n".join(lines) + "\n").encode("utf-8")


PROJECT_INSIGHTS_EXPORTERS = {"json": insights_json, "csv": project_insights_csv, "md": project_insights_md}
OVERVIEW_EXPORTERS = {"json": insights_json, "csv": overview_csv, "md": overview_md}

CONTENT_TYPE = {
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
    "md": "text/markdown; charset=utf-8",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
}
