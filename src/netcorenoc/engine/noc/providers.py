"""The AI providers an agent can run on, and the vault credential each one's key is (ADR #471).

An agent the appliance runs itself is woken through one provider's API with that provider's key,
and the key is a vault credential like any device's: sealed twice, bound to where it works, read by
a custodian only after a step-up — and never shown to an agent, which receives the model's answers,
not the key. Each provider here is one credential **kind** (`kinds.KINDS`, group ``ai``) whose one
field is the API key, so a person adds a key by picking a logo and pasting it, nothing else.

* **Anthropic** (Claude) is the `claude` adapter, the Anthropic SDK (ADR #467).
* **OpenAI, Google AI Studio (Gemini), xAI (Grok), Meta (Llama API) and Ollama** are the `openai`
  adapter: each serves the OpenAI Chat Completions API at its own base path, so one loop
  (`api/agent_openai.py`) drives them all.
* **A cloud provider's endpoint is fixed**: its key works at one host, so the vault refuses to bind
  it anywhere else (:func:`check_endpoint`) — a key pasted for OpenAI cannot be pointed at another
  machine. **Ollama runs where its owner runs it** (this host, a GPU box on the LAN), so its host
  and port are the custodian's, and it needs no key at all.

Model names are not listed here, apart from Anthropic's (`agents.MODELS`, which carries their
prices): a provider's catalogue moves faster than a release, so the console asks the provider
(`POST /api/noc/secrets/{id}/models`) and suggests one example meanwhile.

Pure.
"""

from __future__ import annotations

from dataclasses import dataclass

from netcorenoc.engine.noc.agents import MODELS, OPENAI_PROVIDERS

__all__ = [
    "PROVIDERS",
    "Provider",
    "ProviderError",
    "base_url",
    "by_kind",
    "check_endpoint",
    "describe",
]


class ProviderError(ValueError):
    """An endpoint a provider's key may not be bound to; the message names the field."""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    #: What its models are called, as people say it ("Claude", "Gemini").
    product: str
    adapter: str  # "claude" | "openai"
    host: str
    port: int
    scheme: str  # "HTTPS" | "HTTP"
    #: Where its OpenAI-compatible API lives below the host (Anthropic: its own API's root).
    base_path: str
    key_required: bool
    #: How its keys begin, to recognise a paste (shown, never enforced).
    key_hint: str
    #: Where its owner creates a key.
    key_url: str
    example_model: str
    #: False for a cloud API (one host serves it); True for one its owner runs (Ollama).
    own_host: bool = False

    @property
    def kind(self) -> str:
        return f"ai_{self.id}"


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            "anthropic",
            "Anthropic",
            "Claude",
            "claude",
            "api.anthropic.com",
            443,
            "HTTPS",
            "/v1",
            True,
            "sk-ant-",
            "https://console.anthropic.com/settings/keys",
            next(iter(MODELS)),
        ),
        Provider(
            "openai",
            "OpenAI",
            "GPT",
            "openai",
            "api.openai.com",
            443,
            "HTTPS",
            "/v1",
            True,
            "sk-",
            "https://platform.openai.com/api-keys",
            "gpt-4.1",
        ),
        Provider(
            "google",
            "Google AI Studio",
            "Gemini",
            "openai",
            "generativelanguage.googleapis.com",
            443,
            "HTTPS",
            "/v1beta/openai",
            True,
            "AIza",
            "https://aistudio.google.com/apikey",
            "gemini-2.5-flash",
        ),
        Provider(
            "xai",
            "xAI",
            "Grok",
            "openai",
            "api.x.ai",
            443,
            "HTTPS",
            "/v1",
            True,
            "xai-",
            "https://console.x.ai",
            "grok-4",
        ),
        Provider(
            "meta",
            "Meta",
            "Llama",
            "openai",
            "api.llama.com",
            443,
            "HTTPS",
            "/compat/v1",
            True,
            "LLM|",
            "https://llama.developer.meta.com",
            "Llama-4-Maverick-17B-128E-Instruct-FP8",
        ),
        Provider(
            "ollama",
            "Ollama",
            "local models",
            "openai",
            "127.0.0.1",
            11434,
            "HTTP",
            "/v1",
            False,
            "",
            "https://ollama.com/download",
            "llama3.1",
            own_host=True,
        ),
    )
}


# The agents' configuration names the `openai` adapter's providers without importing this module.
assert tuple(p.id for p in PROVIDERS.values() if p.adapter == "openai") == OPENAI_PROVIDERS


def by_kind(kind: str) -> Provider | None:
    """The provider whose key a credential of ``kind`` is, or None for a device credential."""
    return next((p for p in PROVIDERS.values() if p.kind == kind), None)


def check_endpoint(kind: str, host: str, port: int) -> None:
    """Refuse to bind a cloud provider's key anywhere but its own API."""
    provider = by_kind(kind)
    if provider is None or provider.own_host:
        return
    if host != provider.host or int(port) != provider.port:
        raise ProviderError(
            f"host: a key for {provider.label} works only at {provider.host}:{provider.port}"
        )


def base_url(provider: Provider, host: str, port: int, scheme: str) -> str:
    """Where the provider's API answers, for a credential bound to ``host``:``port``."""
    proto = "https" if scheme.upper() == "HTTPS" else "http"
    default = (proto == "https" and port == 443) or (proto == "http" and port == 80)
    where = host if default else f"{host}:{port}"
    if ":" in host and not host.startswith("["):
        where = f"[{host}]" if default else f"[{host}]:{port}"
    return f"{proto}://{where}{provider.base_path}"


def describe() -> list[dict[str, object]]:
    """The providers as the console's cards and the hiring form read them."""
    return [
        {
            "id": p.id,
            "label": p.label,
            "product": p.product,
            "kind": p.kind,
            "adapter": p.adapter,
            "host": p.host,
            "port": p.port,
            "scheme": p.scheme,
            "key_required": p.key_required,
            "key_hint": p.key_hint,
            "key_url": p.key_url,
            "example_model": p.example_model,
            "own_host": p.own_host,
            "models": list(MODELS) if p.adapter == "claude" else [],
        }
        for p in PROVIDERS.values()
    ]
