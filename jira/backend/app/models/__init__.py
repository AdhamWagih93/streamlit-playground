"""Import all models so SQLAlchemy's metadata is fully populated."""
from app.models.user import User
from app.models.project import Project, ProjectMember, Component, Version
from app.models.issue import (
    IssueType,
    Status,
    StatusCategory,
    Priority,
    Label,
    Issue,
    IssueLink,
    issue_labels,
    issue_components,
    issue_fix_versions,
)
from app.models.agile import Board, Sprint
from app.models.activity import Comment, Attachment, Worklog, IssueHistory, Notification
from app.models.customfield import CustomField, CustomFieldValue, SavedFilter
from app.models.rbac import (
    Group,
    user_groups,
    ProjectRole,
    ProjectRoleActor,
    PermissionScheme,
    PermissionGrant,
    GlobalPermissionGrant,
)
from app.models.identity import MailConfig, JiraConnection, IdentityProvider, AuthSettings
from app.models.sync import ProjectSyncLink, SyncRun
from app.models.notify_prefs import UserNotificationPreference, NOTIFICATION_EVENTS, CHANNELS

__all__ = [
    "User",
    "Project",
    "ProjectMember",
    "Component",
    "Version",
    "IssueType",
    "Status",
    "StatusCategory",
    "Priority",
    "Label",
    "Issue",
    "IssueLink",
    "issue_labels",
    "issue_components",
    "issue_fix_versions",
    "Board",
    "Sprint",
    "Comment",
    "Attachment",
    "Worklog",
    "IssueHistory",
    "Notification",
    "CustomField",
    "CustomFieldValue",
    "SavedFilter",
    "Group",
    "user_groups",
    "ProjectRole",
    "ProjectRoleActor",
    "PermissionScheme",
    "PermissionGrant",
    "GlobalPermissionGrant",
    "MailConfig",
    "JiraConnection",
    "IdentityProvider",
    "AuthSettings",
    "ProjectSyncLink",
    "SyncRun",
    "UserNotificationPreference",
    "NOTIFICATION_EVENTS",
    "CHANNELS",
]
