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
"""

from __future__ import annotations

from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request

from netcorenoc.api import noc_gate as gate
from netcorenoc.api.context import AppContext
from netcorenoc.api.declare import DeclaredRoutes
from netcorenoc.crosscutting import auth, secretbox
from netcorenoc.engine.noc import kinds, llm_http, providers
from netcorenoc.engine.noc.llm_http import ProviderCallError

NOT_A_PROVIDER = "that credential is not an AI provider's key"


def register(app: FastAPI, ctx: AppContext) -> None:
    """Register the AI provider route on `app`."""
    store, security = ctx.store, ctx.security
    audit_row = ctx.perimeter.audit_row
    route = DeclaredRoutes(app)

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
            raise HTTPException(status_code=502, detail=f"{provider.label}: {exc}") from exc
        fit = next((d["name"] for d in details if d["fit"] == "agents"), None)
        return {
            "provider": provider.id,
            "models": models[:500],
            "count": len(models),
            "details": details[:500],
            "recommended": fit,
        }
