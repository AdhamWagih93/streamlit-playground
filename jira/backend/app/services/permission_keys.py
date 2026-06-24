"""Canonical permission keys and holder types.

The project-permission keys deliberately use the same identifiers Jira uses for
its built-in permissions so a Jira permission scheme imports onto a Trackly
scheme with a direct 1:1 key mapping. Holder types likewise mirror Jira's
permission "holder" model (group / user / projectRole / special).
"""
from __future__ import annotations

# --- Global permissions ----------------------------------------------------
ADMINISTER = "ADMINISTER"  # full site administration (instance admin)
SYSTEM_ADMIN = "SYSTEM_ADMIN"
BROWSE_USERS = "USER_PICKER"  # browse/pick users & groups
CREATE_SHARED_OBJECTS = "CREATE_SHARED_OBJECTS"
MANAGE_GROUP_SUBSCRIPTIONS = "MANAGE_GROUP_FILTER_SUBSCRIPTIONS"
BULK_CHANGE = "BULK_CHANGE"

GLOBAL_PERMISSIONS = {
    ADMINISTER: "Administer the whole Trackly instance",
    SYSTEM_ADMIN: "System administration (infrastructure level)",
    BROWSE_USERS: "Browse and pick users and groups",
    CREATE_SHARED_OBJECTS: "Create shared filters and dashboards",
    MANAGE_GROUP_SUBSCRIPTIONS: "Manage group filter subscriptions",
    BULK_CHANGE: "Make bulk changes to issues",
}

# --- Project permissions (Jira-compatible keys) ----------------------------
ADMINISTER_PROJECTS = "ADMINISTER_PROJECTS"
BROWSE_PROJECTS = "BROWSE_PROJECTS"
CREATE_ISSUES = "CREATE_ISSUES"
EDIT_ISSUES = "EDIT_ISSUES"
DELETE_ISSUES = "DELETE_ISSUES"
ASSIGN_ISSUES = "ASSIGN_ISSUES"
ASSIGNABLE_USER = "ASSIGNABLE_USER"
TRANSITION_ISSUES = "TRANSITION_ISSUES"
RESOLVE_ISSUES = "RESOLVE_ISSUES"
CLOSE_ISSUES = "CLOSE_ISSUES"
MODIFY_REPORTER = "MODIFY_REPORTER"
MOVE_ISSUES = "MOVE_ISSUES"
LINK_ISSUES = "LINK_ISSUES"
SCHEDULE_ISSUES = "SCHEDULE_ISSUES"
ADD_COMMENTS = "ADD_COMMENTS"
EDIT_ALL_COMMENTS = "EDIT_ALL_COMMENTS"
EDIT_OWN_COMMENTS = "EDIT_OWN_COMMENTS"
DELETE_ALL_COMMENTS = "DELETE_ALL_COMMENTS"
DELETE_OWN_COMMENTS = "DELETE_OWN_COMMENTS"
CREATE_ATTACHMENTS = "CREATE_ATTACHMENTS"
DELETE_ALL_ATTACHMENTS = "DELETE_ALL_ATTACHMENTS"
DELETE_OWN_ATTACHMENTS = "DELETE_OWN_ATTACHMENTS"
WORK_ON_ISSUES = "WORK_ON_ISSUES"
EDIT_OWN_WORKLOGS = "EDIT_OWN_WORKLOGS"
EDIT_ALL_WORKLOGS = "EDIT_ALL_WORKLOGS"
DELETE_OWN_WORKLOGS = "DELETE_OWN_WORKLOGS"
DELETE_ALL_WORKLOGS = "DELETE_ALL_WORKLOGS"
MANAGE_SPRINTS = "MANAGE_SPRINTS"
MANAGE_WATCHERS = "MANAGE_WATCHERS"
VIEW_VOTERS_AND_WATCHERS = "VIEW_VOTERS_AND_WATCHERS"

PROJECT_PERMISSIONS = {
    ADMINISTER_PROJECTS: "Administer the project (settings, roles, components, versions)",
    BROWSE_PROJECTS: "Browse the project and view its issues",
    CREATE_ISSUES: "Create issues",
    EDIT_ISSUES: "Edit issues",
    DELETE_ISSUES: "Delete issues",
    ASSIGN_ISSUES: "Assign issues to other users",
    ASSIGNABLE_USER: "Be assigned issues",
    TRANSITION_ISSUES: "Transition issues through the workflow",
    RESOLVE_ISSUES: "Resolve and reopen issues",
    CLOSE_ISSUES: "Close issues",
    MODIFY_REPORTER: "Modify the reporter of an issue",
    MOVE_ISSUES: "Move issues between projects",
    LINK_ISSUES: "Link issues to one another",
    SCHEDULE_ISSUES: "Schedule issues (set due dates)",
    ADD_COMMENTS: "Add comments",
    EDIT_ALL_COMMENTS: "Edit any comment",
    EDIT_OWN_COMMENTS: "Edit own comments",
    DELETE_ALL_COMMENTS: "Delete any comment",
    DELETE_OWN_COMMENTS: "Delete own comments",
    CREATE_ATTACHMENTS: "Add attachments",
    DELETE_ALL_ATTACHMENTS: "Delete any attachment",
    DELETE_OWN_ATTACHMENTS: "Delete own attachments",
    WORK_ON_ISSUES: "Log work on issues",
    EDIT_OWN_WORKLOGS: "Edit own worklogs",
    EDIT_ALL_WORKLOGS: "Edit any worklog",
    DELETE_OWN_WORKLOGS: "Delete own worklogs",
    DELETE_ALL_WORKLOGS: "Delete any worklog",
    MANAGE_SPRINTS: "Create and manage sprints",
    MANAGE_WATCHERS: "Manage watchers on issues",
    VIEW_VOTERS_AND_WATCHERS: "View voters and watchers",
}

ALL_PERMISSIONS = {**GLOBAL_PERMISSIONS, **PROJECT_PERMISSIONS}

# --- Holder types (who a grant is given to) --------------------------------
HOLDER_GROUP = "group"            # holder_value = group name
HOLDER_USER = "user"              # holder_value = user id (or external id on import)
HOLDER_PROJECT_ROLE = "role"      # holder_value = project role id/name
HOLDER_SPECIAL = "special"        # holder_value = one of the SPECIAL_* below

# Special, dynamic holders evaluated per-issue/per-project.
SPECIAL_REPORTER = "reporter"
SPECIAL_ASSIGNEE = "assignee"
SPECIAL_PROJECT_LEAD = "projectLead"
SPECIAL_CURRENT_USER = "currentUser"   # any authenticated user
SPECIAL_ANYONE = "anyone"              # public / anonymous

HOLDER_TYPES = {HOLDER_GROUP, HOLDER_USER, HOLDER_PROJECT_ROLE, HOLDER_SPECIAL}

# Map Jira permission-scheme holder "type" strings onto Trackly holder types.
# Used by the Jira sync/import to translate grants faithfully.
JIRA_HOLDER_MAP = {
    "group": (HOLDER_GROUP, None),
    "user": (HOLDER_USER, None),
    "projectRole": (HOLDER_PROJECT_ROLE, None),
    "applicationRole": (HOLDER_SPECIAL, SPECIAL_CURRENT_USER),
    "reporter": (HOLDER_SPECIAL, SPECIAL_REPORTER),
    "assignee": (HOLDER_SPECIAL, SPECIAL_ASSIGNEE),
    "currentAssignee": (HOLDER_SPECIAL, SPECIAL_ASSIGNEE),
    "lead": (HOLDER_SPECIAL, SPECIAL_PROJECT_LEAD),
    "projectLead": (HOLDER_SPECIAL, SPECIAL_PROJECT_LEAD),
    "loggedin": (HOLDER_SPECIAL, SPECIAL_CURRENT_USER),
    "anyone": (HOLDER_SPECIAL, SPECIAL_ANYONE),
}
