"""NOC → Secrets → AI providers: what a provider's key can run (ADR #471).

One route, beside the vault's: **the models a stored key may use**, asked of the provider with
that key, so the hiring form offers what exists instead of a name typed from memory. It is the
vault's gate — a custodian, signed in as a person — because it opens the key; it takes no step-up
because nothing comes back but the provider's public model names, and the key goes only to the
host the credential is bound to (`engine/noc/llm_http.py`). Every call is a `noc.secret.use` audit
row naming who asked.

**Ollama answers in its own words** (ADR #477): the models pulled on that machine, what each can
do, which are loaded now, ranked so the first suits an agent (`details`, `recommended`). Should an
Ollama endpoint not speak its native API (a proxy that passes only `/v1`), the OpenAI list stands.

ADR #487 adds the two things that made a local model easy elsewhere (AnythingLLM, Paperclip's
Ollama adapters):

* ``GET /api/noc/providers/ollama/detect`` — **where is Ollama?** The appliance tries the
  addresses a local Ollama usually answers at (this host, the container's host, a compose service
  called `ollama`, the Docker bridge, the container's gateway) and says which answered, with its
  version and models. The list is the appliance's own; nothing a caller types is dialled.
* ``POST /api/noc/secrets/{id}/try`` — **does this model work here?** One short turn with one tool,
  timed: did it answer, did it call the tool. A model that answers but never calls a tool cannot
  work a case, and this says so before an agent is hired on it.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, secretbox
from netcorenoc.engine.noc import kinds, llm_http, ollama_chat, providers
from netcorenoc.engine.noc.llm_http import ProviderCallError

NOT_A_PROVIDER = "that credential is not an AI provider's key"
#: The one tool a try offers, and what the model is asked to do with it.
TRY_TOOL = {
    "type": "function",
    "function": {
        "name": "get_case_status",
        "description": "Read the status of a NOC case by its number.",
        "parameters": {
            "type": "object",
            "properties": {"case": {"type": "integer", "description": "The case number"}},
            "required": ["case"],
        },
    },
}
TRY_PROMPT = "Check the status of case 42 with the tool you have. Do not answer from memory."
TRY_TIMEOUT_S = 180.0


class TryIn(BaseModel):
    """Which model to try, and with what context window (Ollama only)."""

    model_config = ConfigDict(extra="forbid")

    model: str = Field(min_length=1, max_length=200)
    num_ctx: int = Field(default=ollama_chat.DEFAULT_NUM_CTX, ge=2048, le=131072)


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the AI provider route on `app`."""
    store, security = ctx.store, ctx.security
    audit_row = ctx.perimeter.audit_row
    route = DeclaredRoutes(app)

    @route.get("/api/noc/providers/ollama/detect", operation_id="noc_ollama_detect")
    async def detect_ollama(
        request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """Where a local Ollama answers, tried at the appliance's own list of usual addresses. Like
        the vault's list, a caller who is not a custodian is told why, and nothing is tried."""
        if not await gate.is_custodian(ctx, request, principal):
            return {"custodian": False, "explanation": gate.NOT_CUSTODIAN, "found": [], "tried": []}
        candidates = providers.ollama_candidates()
        found = await llm_http.ollama_probe(candidates)
        return {
            "custodian": True,
            "found": found,
            "tried": [f"{h}:{p}" for h, p in candidates],
            "in_container": providers.in_container(),
        }

    async def opened(secret_id: int) -> tuple[dict[str, Any], dict[str, str], Any]:
        """A stored AI credential, opened inside the appliance: its row, fields and provider."""
        async with store.reading():
            secret = await store.noc_get_secret(secret_id, sealed=True)
        if secret is None:
            raise HTTPException(status_code=404, detail="no such credential")
        provider = providers.by_kind(str(secret["kind"]))
        if provider is None:
            raise HTTPException(status_code=400, detail=NOT_A_PROVIDER)
        fields = secretbox.current().open(
            kinds.binding(
                str(secret["ref"]),
                str(secret["kind"]),
                str(secret["host"]),
                int(secret["port"]),
                str(secret["protocol_version"]),
                str(secret["access_level"]),
            ),
            str(secret["sealed"]),
        )
        return secret, fields, provider

    @route.post("/api/noc/secrets/{secret_id}/try", operation_id="noc_provider_try")
    async def try_model(
        secret_id: int, body: TryIn, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """One short, timed turn with one tool: does this model answer, and does it call tools?"""
        await gate.require_custodian(ctx, request, principal)
        secret, fields, provider = await opened(secret_id)
        base = providers.base_url(
            provider, str(secret["host"]), int(secret["port"]), str(secret["protocol_version"])
        )
        async with ctx.write_txn():
            await audit_row(
                request,
                principal,
                "noc.secret.use",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={"provider": provider.id, "for": "try", "model": body.model},
            )
        payload: dict[str, Any] = {
            "model": body.model,
            "messages": [{"role": "user", "content": TRY_PROMPT}],
            "tools": [TRY_TOOL],
            "tool_choice": "auto",
            "max_tokens": 512,
        }
        if provider.ollama:
            payload["num_ctx"] = body.num_ctx
        started = time.monotonic()
        try:
            answer = await llm_http.chat(
                provider, base, str(fields.get("api_key") or ""), payload, timeout=TRY_TIMEOUT_S
            )
        except ProviderCallError as exc:
            hint = providers.loopback_hint(str(secret["host"]))
            return {"ok": False, "error": f"{provider.label}: {exc}{hint}"[:400]}
        seconds = round(time.monotonic() - started, 1)
        choices = answer.get("choices") or [{}]
        message = choices[0].get("message") or {}
        called = [c for c in message.get("tool_calls") or [] if isinstance(c, dict)]
        return {
            "ok": True,
            "seconds": seconds,
            "called_tool": bool(called),
            "reply": str(message.get("content") or "")[:300],
            "prompt_tokens": int((answer.get("usage") or {}).get("prompt_tokens") or 0),
        }

    @route.post("/api/noc/secrets/{secret_id}/models", operation_id="noc_provider_models")
    async def provider_models(
        secret_id: int, request: Request, principal: auth.Principal = Depends(security)
    ) -> dict[str, Any]:
        """The models this AI provider key may use, as the provider lists them."""
        await gate.require_custodian(ctx, request, principal)
        async with store.reading():
            secret = await store.noc_get_secret(secret_id, sealed=True)
        if secret is None:
            raise HTTPException(status_code=404, detail="no such credential")
        provider = providers.by_kind(str(secret["kind"]))
        if provider is None:
            raise HTTPException(status_code=400, detail=NOT_A_PROVIDER)
        fields = secretbox.current().open(
            kinds.binding(
                str(secret["ref"]),
                str(secret["kind"]),
                str(secret["host"]),
                int(secret["port"]),
                str(secret["protocol_version"]),
                str(secret["access_level"]),
            ),
            str(secret["sealed"]),
        )
        base = providers.base_url(
            provider, str(secret["host"]), int(secret["port"]), str(secret["protocol_version"])
        )
        async with ctx.write_txn():
            await audit_row(
                request,
                principal,
                "noc.secret.use",
                "ok",
                object_type="noc_secret",
                object_id=str(secret_id),
                details={"provider": provider.id, "for": "models"},
            )
        key = str(fields.get("api_key") or "")
        details: list[dict[str, Any]] = []
        try:
            if provider.ollama:
                root = providers.api_root(
                    str(secret["host"]), int(secret["port"]), str(secret["protocol_version"])
                )
                try:
                    details = await llm_http.ollama_models(provider, root, key)
                except ProviderCallError:
                    details = []
            models = (
                [d["name"] for d in details]
                if details
                else await llm_http.list_models(provider, base, key)
            )
        except ProviderCallError as exc:
            hint = providers.loopback_hint(str(secret["host"]))
            raise HTTPException(status_code=502, detail=f"{provider.label}: {exc}{hint}") from exc
        fit = next((d["name"] for d in details if d["fit"] == "agents"), None)
        return {
            "provider": provider.id,
            "models": models[:500],
            "count": len(models),
            "details": details[:500],
            "recommended": fit,
        }
