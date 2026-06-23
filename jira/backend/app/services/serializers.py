"""Shared ORM -> schema serializers.

Centralised so every router (issues, agile, search) emits identical issue
payloads. The only field that needs massaging is ``labels`` (Label objects ->
list of names); everything else is handled by Pydantic's from_attributes.
"""
from __future__ import annotations

from app.models import Issue
from app.schemas.issue import (
    AttachmentOut,
    CommentOut,
    IssueDetail,
    IssueLinkOut,
    IssueListItem,
    IssueRef,
    WorklogOut,
)

# Inverse link labels so an inward link reads naturally from the other side.
_INVERSE = {
    "blocks": "is_blocked_by",
    "is_blocked_by": "blocks",
    "duplicates": "is_duplicated_by",
    "is_duplicated_by": "duplicates",
    "clones": "is_cloned_by",
    "is_cloned_by": "clones",
    "relates_to": "relates_to",
}


def issue_ref(issue: Issue) -> IssueRef:
    return IssueRef.model_validate(issue)


def to_list_item(issue: Issue) -> IssueListItem:
    item = IssueListItem.model_validate(issue)
    item.labels = [l.name for l in issue.labels]
    return item


def to_detail(issue: Issue) -> IssueDetail:
    detail = IssueDetail.model_validate(issue)
    detail.labels = [l.name for l in issue.labels]
    detail.comments = [CommentOut.model_validate(c) for c in issue.comments]
    detail.attachments = [AttachmentOut.model_validate(a) for a in issue.attachments]
    detail.worklogs = [WorklogOut.model_validate(w) for w in issue.worklogs]
    detail.subtasks = [issue_ref(s) for s in getattr(issue, "subtasks", [])]

    links: list[IssueLinkOut] = []
    for link in getattr(issue, "outward_links", []):
        links.append(IssueLinkOut(id=link.id, link_type=link.link_type, issue=issue_ref(link.target)))
    for link in getattr(issue, "inward_links", []):
        links.append(
            IssueLinkOut(
                id=link.id,
                link_type=_INVERSE.get(link.link_type, link.link_type),
                issue=issue_ref(link.source),
            )
        )
    detail.links = links
    return detail
