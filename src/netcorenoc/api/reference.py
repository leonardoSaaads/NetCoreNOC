"""The API reference: one sentence and one group per route, for people writing programs (v0.30.0).

`DeclaredRoutes` puts each entry into the OpenAPI schema as the operation's `summary` and `tags`,
beside two extensions read from the authorization tables — `x-capability` and `x-scope` — so the
schema says what every route does **and** what a token needs to call it. People & access → Service
tokens draws its reference from that schema, through `GET /api/reference` (`tokens.manage`): one
source, the running appliance, and `tests/api/test_reference.py` fails when a route has no entry
here or an entry has no route.

Written for the reader of the reference, not of the code: what the call returns or changes, in the
console's words.
"""

from __future__ import annotations

from typing import NamedTuple


class Entry(NamedTuple):
    group: str
    summary: str


ACCOUNT, SITUATIONS, ALARMS, MONITOR = "Account", "Situations", "Alarms", "Monitoring"
ELEMENTS, CATALOGUE, MAINT, MODELS = "Network elements", "Trap catalogue", "Maintenance", "Models"
ADMIN, ACCESS, AUDIT, SETTINGS = "People", "Access policy", "Audit", "Settings"
PUBLIC = "Sign-in and recovery"

REFERENCE: dict[tuple[str, str], Entry] = {
    ("POST", "/api/login"): Entry(PUBLIC, "Sign in with a username and password (sets a cookie)"),
    ("GET", "/api/login/options"): Entry(PUBLIC, "What the sign-in screen may offer"),
    ("POST", "/api/password-reset"): Entry(PUBLIC, "Ask for a password-reset link by email"),
    ("POST", "/api/password-reset/confirm"): Entry(PUBLIC, "Set a new password from a reset link"),
    ("POST", "/api/logout"): Entry(ACCOUNT, "Sign out of this session"),
    ("GET", "/api/me"): Entry(ACCOUNT, "Who is calling: role, capabilities and visibility"),
    ("POST", "/api/password"): Entry(ACCOUNT, "Change your own password"),
    ("GET", "/api/avatars/{uid}"): Entry(ACCOUNT, "A person's photo"),
    ("POST", "/api/me/avatar"): Entry(ACCOUNT, "Upload your photo (PNG, WebP or JPEG)"),
    ("DELETE", "/api/me/avatar"): Entry(ACCOUNT, "Remove your photo"),
    ("POST", "/api/me/profile"): Entry(ACCOUNT, "Set your display name and recovery address"),
    ("GET", "/api/stats"): Entry(MONITOR, "Counters, health and warnings for the Overview"),
    ("GET", "/api/graph"): Entry(MONITOR, "The learned network graph: elements and links"),
    ("GET", "/api/classes"): Entry(CATALOGUE, "Alarm classes seen so far"),
    ("GET", "/api/situations"): Entry(SITUATIONS, "List situations (filter by status, element)"),
    ("GET", "/api/situations/{sid}"): Entry(SITUATIONS, "One situation with its alarms"),
    ("GET", "/api/timeline"): Entry(MONITOR, "Raises and clears over time"),
    ("GET", "/api/events"): Entry(MONITOR, "Live updates as server-sent events"),
    ("GET", "/api/entities"): Entry(ELEMENTS, "Network elements and their learned identity"),
    ("GET", "/api/entities/{ne_id}"): Entry(ELEMENTS, "One network element's learned identity"),
    ("GET", "/api/state-clears"): Entry(ELEMENTS, "Learned clear rules per alarm class"),
    ("POST", "/api/entities/{ne_id}/reset"): Entry(ELEMENTS, "Forget an element's learned key"),
    ("POST", "/api/profiles/{ne_id}/reset"): Entry(ELEMENTS, "Wipe an element's varbind evidence"),
    ("POST", "/api/situations/{sid}/feedback"): Entry(SITUATIONS, "Confirm or reject a grouping"),
    ("POST", "/api/labels"): Entry(SITUATIONS, "Name or grade an element or alarm class"),
    ("DELETE", "/api/labels/{kind}/{target_id}"): Entry(SITUATIONS, "Withdraw a name or grade"),
    ("POST", "/api/situations/{sid}/close"): Entry(SITUATIONS, "Close a situation"),
    ("POST", "/api/situations/{sid}/move"): Entry(SITUATIONS, "Move alarms to another situation"),
    ("POST", "/api/situations/{sid}/merge"): Entry(SITUATIONS, "Merge two situations"),
    ("POST", "/api/situations/{sid}/proposal"): Entry(SITUATIONS, "Accept or reject a proposal"),
    ("POST", "/api/situations/{sid}/split"): Entry(SITUATIONS, "Split alarms into a new situation"),
    ("POST", "/api/situations/{sid}/name"): Entry(SITUATIONS, "Rename a situation"),
    ("POST", "/api/situations/{sid}/promote"): Entry(SITUATIONS, "Pick up a new situation (Open)"),
    ("POST", "/api/situations/{sid}/severity"): Entry(SITUATIONS, "Set a situation's severity"),
    ("POST", "/api/alarms/{aid}/clear"): Entry(ALARMS, "Clear one alarm"),
    ("POST", "/api/alarms/clear"): Entry(ALARMS, "Clear every alarm of a situation"),
    ("POST", "/api/alarms/{aid}/outlived/ack"): Entry(ALARMS, "Acknowledge an outlived alarm"),
    ("GET", "/api/users"): Entry(ADMIN, "List accounts"),
    ("POST", "/api/users"): Entry(ADMIN, "Create an account"),
    ("DELETE", "/api/users/{uid}"): Entry(ADMIN, "Delete an account"),
    ("POST", "/api/users/{uid}/role"): Entry(ADMIN, "Change an account's role"),
    ("POST", "/api/users/{uid}/profile"): Entry(ADMIN, "Set a person's name and recovery address"),
    ("POST", "/api/users/{uid}/avatar"): Entry(ADMIN, "Upload a person's photo"),
    ("DELETE", "/api/users/{uid}/avatar"): Entry(ADMIN, "Remove a person's photo"),
    ("GET", "/api/tokens"): Entry(ADMIN, "List service tokens"),
    ("GET", "/api/reference"): Entry(ADMIN, "This reference: the schema, with capabilities"),
    ("POST", "/api/tokens"): Entry(ADMIN, "Create a service token (the value is shown once)"),
    ("DELETE", "/api/tokens/{tid}"): Entry(ADMIN, "Revoke a service token"),
    ("GET", "/api/rbac"): Entry(ACCESS, "The capability policy and what each role holds"),
    ("POST", "/api/rbac"): Entry(ACCESS, "Apply, restore or clear the capability policy"),
    ("POST", "/api/rbac/subject"): Entry(ACCESS, "Narrow one role, person or token"),
    ("GET", "/api/scope"): Entry(ACCESS, "The visibility policy and what each role sees"),
    ("POST", "/api/scope"): Entry(ACCESS, "Apply, restore or clear the visibility policy"),
    ("GET", "/api/config"): Entry(SETTINGS, "Live configuration and where each value comes from"),
    ("POST", "/api/config"): Entry(SETTINGS, "Set the trap allowlist and operational retention"),
    ("GET", "/api/dataset/retention"): Entry(SETTINGS, "The feedback dataset's retention tiers"),
    ("POST", "/api/dataset/retention"): Entry(SETTINGS, "Preview or apply a retention change"),
    ("GET", "/api/snmp"): Entry(SETTINGS, "What the trap receiver accepts: versions, users"),
    ("POST", "/api/snmp"): Entry(SETTINGS, "Set SNMP versions, communities and SNMPv3 users"),
    ("GET", "/api/email"): Entry(SETTINGS, "The SMTP server used for recovery email"),
    ("POST", "/api/email"): Entry(SETTINGS, "Configure the SMTP server"),
    ("POST", "/api/email/test"): Entry(SETTINGS, "Send a test email"),
    ("GET", "/api/scorer"): Entry(MODELS, "The link scorer's parameters and history"),
    ("GET", "/api/correlation"): Entry(MODELS, "How the running scorer is behaving"),
    ("GET", "/api/models"): Entry(MODELS, "Model learning progress and evidence floors"),
    ("POST", "/api/models/register"): Entry(MODELS, "Register one of this appliance's fits"),
    ("POST", "/api/scorer/preview"): Entry(MODELS, "Preview a scorer change on recent alarms"),
    ("POST", "/api/scorer"): Entry(MODELS, "Change the scorer's parameters"),
    ("POST", "/api/scorer/rollback"): Entry(MODELS, "Roll the scorer back to a version"),
    ("GET", "/api/promotion"): Entry(MODELS, "Proposed and refused promotions"),
    ("POST", "/api/promotion"): Entry(MODELS, "Propose a promotion"),
    ("GET", "/api/decider"): Entry(MODELS, "Which model decides links, and why"),
    ("POST", "/api/decider"): Entry(MODELS, "Pin a model, or hand the choice to the judge"),
    ("GET", "/api/autonomy"): Entry(MODELS, "Autonomy's grades and state"),
    ("GET", "/api/autonomy/decisions"): Entry(MODELS, "What autonomy did, act by act"),
    ("POST", "/api/autonomy"): Entry(MODELS, "Switch autonomy grades on or off"),
    ("POST", "/api/autonomy/stop"): Entry(MODELS, "Stop autonomy now (the kill switch)"),
    ("GET", "/api/search"): Entry(MODELS, "The site-training search and its trials"),
    ("POST", "/api/search"): Entry(MODELS, "Start a site-training search"),
    ("POST", "/api/search/stop"): Entry(MODELS, "Stop the running search"),
    ("GET", "/api/judge"): Entry(MODELS, "The judge: the league, ranked on this site's labels"),
    ("GET", "/api/quarantine"): Entry(AUDIT, "Datagrams the receiver refused, and why"),
    ("GET", "/api/audit"): Entry(AUDIT, "The audit log"),
    ("GET", "/api/audit/export"): Entry(AUDIT, "Export the audit log as JSON lines"),
    ("POST", "/api/audit/prune"): Entry(AUDIT, "Prune audit rows past retention"),
    ("GET", "/api/maintenance-windows"): Entry(MAINT, "List maintenance windows"),
    ("POST", "/api/maintenance-windows/preview"): Entry(MAINT, "Dry-run a window: what it covers"),
    ("POST", "/api/maintenance-windows"): Entry(MAINT, "Plan a maintenance window"),
    ("GET", "/api/maintenance-windows/{wid}"): Entry(MAINT, "One maintenance window"),
    ("POST", "/api/maintenance-windows/{wid}"): Entry(MAINT, "Edit a maintenance window"),
    ("POST", "/api/maintenance-windows/{wid}/confirm"): Entry(MAINT, "Confirm a long window"),
    ("POST", "/api/maintenance-windows/{wid}/cancel"): Entry(MAINT, "Cancel a planned window"),
    ("POST", "/api/maintenance-windows/{wid}/end"): Entry(MAINT, "End a running window now"),
    ("POST", "/api/maintenance-windows/{wid}/extend"): Entry(MAINT, "Extend a running window"),
    ("GET", "/api/organizations"): Entry(MAINT, "List organizations (providers)"),
    ("POST", "/api/organizations"): Entry(MAINT, "Create an organization"),
    ("POST", "/api/entities/{ne_id}/organization"): Entry(MAINT, "Assign an element's provider"),
    ("GET", "/api/timezones"): Entry(MAINT, "Time zones this appliance can resolve"),
    ("GET", "/api/resources"): Entry(MONITOR, "Host CPU, memory, storage and queue over time"),
    ("GET", "/api/activity/severity"): Entry(MONITOR, "Alarm activity by severity over time"),
    ("GET", "/api/activity/lanes"): Entry(MONITOR, "Alarm activity per element over time"),
    ("GET", "/api/activity/groups"): Entry(MONITOR, "Raises and clears grouped by class"),
    ("GET", "/api/activity/active"): Entry(MONITOR, "Active alarms over time"),
    ("GET", "/api/activity/top"): Entry(MONITOR, "The busiest elements over time"),
    ("GET", "/api/inventory"): Entry(ELEMENTS, "The inventory: every element and its state"),
    ("GET", "/api/elements/{ne_id}/components"): Entry(ELEMENTS, "An element's learned components"),
    ("GET", "/api/elements/{ne_id}"): Entry(ELEMENTS, "One element, for the graph's panel"),
    ("GET", "/api/notices"): Entry(MONITOR, "Warnings, with your snoozes"),
    ("POST", "/api/notices/snooze"): Entry(MONITOR, "Snooze a warning for yourself"),
    ("DELETE", "/api/notices/snooze/{digest}"): Entry(MONITOR, "Remove a snooze"),
    ("GET", "/api/catalogue"): Entry(CATALOGUE, "Trap OIDs by name and severity"),
    ("GET", "/api/catalogue/tree"): Entry(CATALOGUE, "The OID tree under a node"),
    ("GET", "/api/catalogue/rules"): Entry(CATALOGUE, "Declared and imported trap rules"),
    ("POST", "/api/catalogue/rules"): Entry(CATALOGUE, "Name or grade a trap OID or branch"),
    ("DELETE", "/api/catalogue/rules/{rule_id}"): Entry(CATALOGUE, "Withdraw a trap rule"),
    ("POST", "/api/catalogue/import"): Entry(CATALOGUE, "Import a trap list"),
    ("DELETE", "/api/catalogue/imported"): Entry(CATALOGUE, "Undo every imported rule"),
}
