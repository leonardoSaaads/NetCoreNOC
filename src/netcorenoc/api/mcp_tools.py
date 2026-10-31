"""The MCP tools: which routes an agent reaches, and how each is described to it (ADR #466).

**A tool is a route with a different envelope** (`docs/plans/noc-phase2.md` §3). This table names
the routes an agent works through and how each behaves — read-only, destructive, idempotent, open
to the world — and everything else is derived from the application itself, so it cannot drift:

* the **input schema** is the route's own: its path and query parameters and its request body,
  from the OpenAPI document FastAPI builds from the handlers' signatures and models — flattened
  into one object, with every `$ref` resolved, because a model reads one schema;
* the **description** is the handler's docstring, the same sentence the API reference shows;
* **who may call it** is the route's declared capability: `tools/list` shows a caller only the
  tools it may call (`rbac.ROUTE_PERMISSIONS`), and the call itself is checked again by the route.

`untrusted` marks a tool whose answer carries a device's own text (G6): it is said, beside the
answer, to be data and never instructions.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from netcorenoc.crosscutting import rbac

__all__ = ["TOOLS", "Tool", "build", "split_arguments"]


@dataclass(frozen=True)
class Tool:
    name: str
    method: str
    path: str
    title: str
    read_only: bool = False
    destructive: bool = False
    idempotent: bool = False
    open_world: bool = False
    untrusted: bool = False
    schema: dict[str, Any] = field(default_factory=dict, compare=False)
    description: str = ""
    path_params: tuple[str, ...] = ()
    query_params: tuple[str, ...] = ()
    has_body: bool = False

    @property
    def capability(self) -> str | None:
        return rbac.permission_for(self.method, self.path)

    def listing(self) -> dict[str, Any]:
        """The tool as `tools/list` answers it."""
        return {
            "name": self.name,
            "title": self.title,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {
                "title": self.title,
                "readOnlyHint": self.read_only,
                "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent,
                "openWorldHint": self.open_world,
            },
        }


def _t(
    name: str,
    method: str,
    path: str,
    title: str,
    *,
    read_only: bool = False,
    destructive: bool = False,
    idempotent: bool = False,
    open_world: bool = False,
    untrusted: bool = False,
) -> Tool:
    return Tool(
        name,
        method,
        path,
        title,
        read_only=read_only,
        destructive=destructive,
        idempotent=idempotent,
        open_world=open_world,
        untrusted=untrusted,
    )


_READ: dict[str, bool] = {"read_only": True, "idempotent": True}

#: The routes an agent works through. Order is the order `tools/list` answers in.
TOOLS: tuple[Tool, ...] = (
    _t("noc_me", "GET", "/api/noc/agents/me", "Who am I", **_READ),
    _t("noc_list_cases", "GET", "/api/noc/cases", "List cases", **_READ),
    _t("noc_get_case", "GET", "/api/noc/cases/{sid}", "Get a case", **_READ),
    _t("noc_case_feed", "GET", "/api/noc/cases/{sid}/feed", "Read a case's feed", **_READ),
    _t("noc_overview", "GET", "/api/noc/overview", "The NOC at a glance", **_READ),
    _t("noc_case_kpis", "GET", "/api/noc/cases/kpis", "The NOC's clocks", **_READ),
    _t("noc_take_case", "POST", "/api/noc/cases/{sid}/take", "Take a case", idempotent=True),
    _t("noc_update_case", "POST", "/api/noc/cases/{sid}", "Update the customer update"),
    _t("noc_post_to_case", "POST", "/api/noc/cases/{sid}/feed", "Post to a case's feed"),
    _t(
        "noc_send_customer_update",
        "POST",
        "/api/noc/cases/{sid}/customer-update",
        "Send the saved customer update to the customer",
    ),
    _t("noc_set_case_pending", "POST", "/api/noc/cases/{sid}/status", "Set a case pending"),
    _t("noc_release_case", "POST", "/api/noc/cases/{sid}/release", "Release a case"),
    _t(
        "noc_resolve_case",
        "POST",
        "/api/noc/cases/{sid}/resolve",
        "Resolve a case",
        destructive=True,
    ),
    _t("noc_assign_case", "POST", "/api/noc/cases/{sid}/assign", "Delegate a case"),
    _t("noc_escalate_case", "POST", "/api/noc/cases/{sid}/escalate", "Escalate a case"),
    _t("list_situations", "GET", "/api/situations", "List situations", **_READ),
    _t("get_situation", "GET", "/api/situations/{sid}", "Get a situation", **_READ),
    _t("get_element", "GET", "/api/elements/{ne_id}", "Get a network element", **_READ),
    _t("noc_list_agents", "GET", "/api/noc/agents", "The NOC's organization", **_READ),
    _t("noc_get_agent", "GET", "/api/noc/agents/{agent_id}", "Get an agent", **_READ),
    _t("noc_invoke_agent", "POST", "/api/noc/agents/{agent_id}/invoke", "Wake an agent"),
    _t("noc_pause_agent", "POST", "/api/noc/agents/{agent_id}/pause", "Pause an agent"),
    _t("noc_resume_agent", "POST", "/api/noc/agents/{agent_id}/resume", "Resume an agent"),
    _t("noc_get_run", "GET", "/api/noc/runs/{run_id}", "Get a run", **_READ),
    _t("noc_finish_run", "POST", "/api/noc/runs/{run_id}/finish", "Report a run finished"),
    _t("noc_list_credentials", "GET", "/api/noc/agent", "Credentials I may lease", **_READ),
    _t(
        "noc_lease_credential",
        "POST",
        "/api/noc/secrets/{secret_id}/lease",
        "Lease a credential",
    ),
    _t(
        "noc_release_lease",
        "POST",
        "/api/noc/leases/{lease_id}/revoke",
        "End a lease",
        idempotent=True,
    ),
    _t(
        "noc_exec",
        "POST",
        "/api/noc/exec",
        "Run a read-only command on a device",
        open_world=True,
        untrusted=True,
    ),
    _t("noc_list_playbooks", "GET", "/api/noc/playbooks", "List playbooks", **_READ),
    _t("noc_get_playbook", "GET", "/api/noc/playbooks/{playbook_id}", "Get a playbook", **_READ),
    _t("noc_create_playbook", "POST", "/api/noc/playbooks", "Write a playbook"),
    _t(
        "noc_update_playbook",
        "POST",
        "/api/noc/playbooks/{playbook_id}",
        "Replace a playbook",
    ),
    # ADR #473: the customers' books — read before acting; memory and notes kept as they work.
    _t("noc_list_customers", "GET", "/api/noc/customers", "The NOC's customers", **_READ),
    _t(
        "noc_get_customer",
        "GET",
        "/api/noc/customers/{customer_id}",
        "A customer's book",
        **_READ,
    ),
    _t(
        "noc_update_customer_memory",
        "POST",
        "/api/noc/customers/{customer_id}/memory",
        "Keep a customer's memory",
    ),
    _t(
        "noc_read_customer_doc",
        "GET",
        "/api/noc/customers/{customer_id}/docs/{doc_id}",
        "Read a customer's document",
        untrusted=True,
        **_READ,
    ),
    _t("noc_find_circuits", "GET", "/api/noc/circuits", "Find a circuit", **_READ),
    _t(
        "noc_list_circuits",
        "GET",
        "/api/noc/customers/{customer_id}/circuits",
        "A customer's circuits",
        **_READ,
    ),
    _t(
        "noc_get_circuit",
        "GET",
        "/api/noc/customers/{customer_id}/circuits/{circuit_id}",
        "A circuit and its path",
        **_READ,
    ),
    _t(
        "noc_note_circuit",
        "POST",
        "/api/noc/customers/{customer_id}/circuits/{circuit_id}/notes",
        "Note on a circuit",
    ),
    _t(
        "list_maintenance_windows",
        "GET",
        "/api/maintenance-windows",
        "List maintenance windows",
        **_READ,
    ),
    _t(
        "create_maintenance_window",
        "POST",
        "/api/maintenance-windows",
        "Create a maintenance window",
    ),
)


def _resolve(node: Any, components: Mapping[str, Any], depth: int = 0) -> Any:
    """Inline every `$ref` (a model reads one schema); deep enough for this API's models."""
    if depth > 12:
        return {}
    if isinstance(node, dict):
        if "$ref" in node:
            name = str(node["$ref"]).rsplit("/", 1)[-1]
            return _resolve(copy.deepcopy(components.get(name, {})), components, depth + 1)
        return {k: _resolve(v, components, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(v, components, depth + 1) for v in node]
    return node


def _describe(tool: Tool, openapi: Mapping[str, Any]) -> Tool:
    operation = openapi["paths"][tool.path][tool.method.lower()]
    components = openapi.get("components", {}).get("schemas", {})
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[str] = []
    for param in operation.get("parameters", []):
        name, where = str(param["name"]), param.get("in")
        if where not in ("path", "query"):
            continue
        schema = _resolve(param.get("schema", {}), components)
        schema.pop("title", None)
        properties[name] = schema
        (path_params if where == "path" else query_params).append(name)
        if param.get("required"):
            required.append(name)
    body = operation.get("requestBody", {}).get("content", {}).get("application/json")
    if body is not None:
        model = _resolve(body["schema"], components)
        for name, schema in model.get("properties", {}).items():
            properties[name] = schema
        required += [r for r in model.get("required", []) if r not in required]
    doc = " ".join(str(operation.get("description") or operation.get("summary", "")).split())
    return Tool(
        **{
            **{k: getattr(tool, k) for k in ("name", "method", "path", "title")},
            **{
                k: getattr(tool, k)
                for k in ("read_only", "destructive", "idempotent", "open_world", "untrusted")
            },
            "schema": {
                "type": "object",
                "properties": properties,
                "required": required,
                "additionalProperties": False,
            },
            "description": doc[:1500],
            "path_params": tuple(path_params),
            "query_params": tuple(query_params),
            "has_body": body is not None,
        }
    )


def build(openapi: Mapping[str, Any]) -> dict[str, Tool]:
    """Every tool, described from the application's own OpenAPI document."""
    return {tool.name: _describe(tool, openapi) for tool in TOOLS}


def split_arguments(
    tool: Tool, arguments: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any] | None]:
    """A tool's flat arguments, back into the route's path, query and body."""
    path = {k: arguments[k] for k in tool.path_params if k in arguments}
    query = {
        k: arguments[k] for k in tool.query_params if k in arguments and arguments[k] is not None
    }
    rest = {k: v for k, v in arguments.items() if k not in path and k not in query}
    return path, query, (rest if tool.has_body else None)
