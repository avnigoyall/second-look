"""Spend ledger for the Social Trend Radar agent and its tools.

Every paid call (Claude, Vultr) goes through this module:

    radar_spend.check_budget("claude")                 # before the call; raises BudgetExceeded at 100 percent
    response = client.messages.create(...)
    radar_spend.check_and_record("claude", model, input_tokens, output_tokens, ...)   # after the call

- Caps: config/radar_spend_caps.json (USD per provider for the whole run). Warn at warn_fraction (0.8), refuse
  at 100 percent.
- Prices: config/radar_prices.json, copied from official pages with source URL and fetch date. A model that is
  missing or has a null price raises UnpricedModel; the call must not be made.
- Ledger: .social_runs/spend_ledger.json (git-ignored), one entry per call with provider, model, tokens,
  estimated cost and running total. Read-modify-write under an exclusive fcntl lock, because several processes
  may write it. A corrupt ledger raises LedgerError and blocks calls; it is never reset to zero.
- The Vultr check also counts the existing embedding guard ledger (.social_runs/vultr_usage.json, owned by
  backend/services/social_embeddings.py) read-only, so the 100 USD Vultr cap covers the whole run.
- Totals are this code's estimates. The provider usage pages are the true numbers.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
CAPS_PATH = ROOT / "config" / "radar_spend_caps.json"
PRICES_PATH = ROOT / "config" / "radar_prices.json"
LEDGER_PATH = ROOT / ".social_runs" / "spend_ledger.json"
VULTR_EMBED_LEDGER_PATH = ROOT / ".social_runs" / "vultr_usage.json"
RESUME_TEMPLATE = ".venv/bin/python scripts/radar_agent.py --resume --run-id {run_id}"


class SpendError(RuntimeError):
    """Base class. Any SpendError means: do not make the paid call."""


class BudgetExceeded(SpendError):
    def __init__(self, provider: str, total_usd: float, cap_usd: float, resume: str):
        self.provider, self.total_usd, self.cap_usd, self.resume = provider, total_usd, cap_usd, resume
        super().__init__(
            f"{provider} spend cap reached: estimated {total_usd:.4f} USD of {cap_usd:.2f} USD. "
            f"Stopped cleanly before the call. To resume after raising the cap in {CAPS_PATH.name} "
            f"(or on a new budget): {resume}")


class UnpricedModel(SpendError):
    pass


class LedgerError(SpendError):
    pass


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def load_caps(path: Path | None = None) -> dict:
    return json.loads(Path(path or CAPS_PATH).read_text())


def load_prices(path: Path | None = None) -> dict:
    return json.loads(Path(path or PRICES_PATH).read_text())


def _tier(prices: dict, provider: str, model: str, prompt_tokens: int) -> dict:
    spec = prices.get("providers", {}).get(provider, {}).get("models", {}).get(model)
    if not spec or not spec.get("tiers"):
        raise UnpricedModel(f"no official price for {provider}/{model} in {PRICES_PATH.name}; call refused")
    for tier in spec["tiers"]:
        limit = tier.get("max_prompt_tokens")
        if limit is None or prompt_tokens <= limit:
            return tier
    return spec["tiers"][-1]


def _rate(tier: dict, key: str, provider: str, model: str, tokens: int) -> float:
    if not tokens:
        return 0.0
    value = tier.get(key)
    if value is None:
        raise UnpricedModel(f"{provider}/{model} has no price for {key}; call refused")
    return tokens * float(value) / 1_000_000


def estimate_cost(provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0, *,
                  cache_write_5m_tokens: int = 0, cache_write_1h_tokens: int = 0, cache_read_tokens: int = 0,
                  prices: dict | None = None) -> float:
    """Estimated USD for one call. input_tokens excludes cache tokens (Anthropic's convention)."""
    prices = prices or load_prices()
    prompt = input_tokens + cache_write_5m_tokens + cache_write_1h_tokens + cache_read_tokens
    tier = _tier(prices, provider, model, prompt)
    return (_rate(tier, "input_per_mtok", provider, model, input_tokens)
            + _rate(tier, "cache_write_5m_per_mtok", provider, model, cache_write_5m_tokens)
            + _rate(tier, "cache_write_1h_per_mtok", provider, model, cache_write_1h_tokens)
            + _rate(tier, "cache_read_per_mtok", provider, model, cache_read_tokens)
            + _rate(tier, "output_per_mtok", provider, model, output_tokens))


def usage_from_anthropic(usage) -> dict:
    """Token counts from an anthropic SDK Usage object (or a dict with the same keys)."""
    get = (lambda k: usage.get(k)) if isinstance(usage, dict) else (lambda k: getattr(usage, k, None))
    cc = get("cache_creation")
    if cc is not None and not isinstance(cc, dict):
        cc = cc.model_dump() if hasattr(cc, "model_dump") else dict(cc)
    w5 = (cc or {}).get("ephemeral_5m_input_tokens") or 0
    w1 = (cc or {}).get("ephemeral_1h_input_tokens") or 0
    if not (w5 or w1):
        w5 = get("cache_creation_input_tokens") or 0
    return {"input_tokens": get("input_tokens") or 0, "output_tokens": get("output_tokens") or 0,
            "cache_write_5m_tokens": w5, "cache_write_1h_tokens": w1,
            "cache_read_tokens": get("cache_read_input_tokens") or 0}


def usage_from_langchain(usage_metadata: dict) -> dict:
    """Token counts from a LangChain AIMessage.usage_metadata. langchain-anthropic adds cache tokens into
    input_tokens, so they are subtracted back out here to avoid double counting."""
    details = usage_metadata.get("input_token_details") or {}
    read = details.get("cache_read") or 0
    w5 = details.get("ephemeral_5m_input_tokens") or 0
    w1 = details.get("ephemeral_1h_input_tokens") or 0
    if not (w5 or w1):
        w5 = details.get("cache_creation") or 0
    base = max(0, (usage_metadata.get("input_tokens") or 0) - read - w5 - w1)
    return {"input_tokens": base, "output_tokens": usage_metadata.get("output_tokens") or 0,
            "cache_write_5m_tokens": w5, "cache_write_1h_tokens": w1, "cache_read_tokens": read}


class Ledger:
    def __init__(self, path: Path | None = None, caps_path: Path | None = None, prices_path: Path | None = None,
                 vultr_embed_ledger: Path | None = VULTR_EMBED_LEDGER_PATH):
        self.path = Path(path or os.getenv("RADAR_SPEND_LEDGER") or LEDGER_PATH)
        self.caps_path = caps_path
        self.prices_path = prices_path
        self.vultr_embed_ledger = Path(vultr_embed_ledger) if vultr_embed_ledger else None

    # ----- storage -----

    def _read(self) -> dict:
        if not self.path.exists():
            return {"entries": [], "totals_usd": {}, "created_at": _now()}
        try:
            data = json.loads(self.path.read_text())
            if not isinstance(data.get("entries"), list) or not isinstance(data.get("totals_usd"), dict):
                raise ValueError("missing entries or totals_usd")  # noqa: TRY004
            return data
        except Exception as exc:
            raise LedgerError(f"spend ledger {self.path} is unreadable ({type(exc).__name__}); calls are blocked "
                              "until it is repaired by hand") from exc

    def _locked(self, change=None) -> dict:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path.with_suffix(".lock"), "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                data = self._read()
                if change is not None:
                    change(data)
                    data["updated_at"] = _now()
                    tmp = self.path.with_suffix(".tmp")
                    tmp.write_text(json.dumps(data, indent=2))
                    os.replace(tmp, self.path)
                return data
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def external_usd(self, provider: str) -> float:
        """Spend recorded by other guards for the same provider (read-only)."""
        if provider != "vultr" or not self.vultr_embed_ledger or not self.vultr_embed_ledger.exists():
            return 0.0
        try:
            data = json.loads(self.vultr_embed_ledger.read_text())
            return float(data["tokens"]) * float(data["price_per_mtok"]) / 1_000_000
        except Exception as exc:
            raise LedgerError(f"Vultr embed ledger {self.vultr_embed_ledger} is unreadable; calls are blocked") from exc

    # ----- public -----

    def status(self, provider: str) -> dict:
        caps = load_caps(self.caps_path)
        cap = caps.get("providers", {}).get(provider, {}).get("cap_usd")
        if cap is None:
            raise SpendError(f"no spend cap for provider {provider!r} in {CAPS_PATH.name}; call refused")
        own = float(self._locked().get("totals_usd", {}).get(provider, 0.0))
        external = self.external_usd(provider)
        total = own + external
        warn_at = float(caps.get("warn_fraction", 0.8)) * float(cap)
        return {"provider": provider, "cap_usd": float(cap), "ledger_usd": round(own, 6),
                "external_usd": round(external, 6), "total_usd": round(total, 6),
                "fraction": total / float(cap) if cap else 1.0, "warn": total >= warn_at,
                "exceeded": total >= float(cap)}

    def check_budget(self, provider: str, estimated_cost_usd: float = 0.0, *, run_id: str | None = None,
                     resume: str | None = None) -> dict:
        """Call before every paid request. Raises BudgetExceeded when the running total (plus the optional
        estimate for the coming call) reaches the cap. Logs a warning at warn_fraction."""
        st = self.status(provider)
        if st["total_usd"] + max(0.0, estimated_cost_usd) >= st["cap_usd"]:
            raise BudgetExceeded(provider, st["total_usd"], st["cap_usd"],
                                 resume or RESUME_TEMPLATE.format(run_id=run_id or "<run_id>"))
        if st["warn"]:
            log.warning("%s spend at %.0f%% of the %.2f USD cap (estimated %.4f USD)", provider,
                        100 * st["fraction"], st["cap_usd"], st["total_usd"])
        return st

    def check_and_record(self, provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0, *,
                         cache_write_5m_tokens: int = 0, cache_write_1h_tokens: int = 0,
                         cache_read_tokens: int = 0, run_id: str | None = None, style: str | None = None,
                         note: str | None = None) -> dict:
        """Record one finished call (its tokens were already spent, so it is always recorded). Returns the
        entry with warn/exceeded flags; the next check_budget refuses once the cap is reached."""
        cost = estimate_cost(provider, model, input_tokens, output_tokens,
                             cache_write_5m_tokens=cache_write_5m_tokens, cache_write_1h_tokens=cache_write_1h_tokens,
                             cache_read_tokens=cache_read_tokens, prices=load_prices(self.prices_path))
        entry = {"ts": _now(), "provider": provider, "model": model, "input_tokens": int(input_tokens),
                 "output_tokens": int(output_tokens), "cache_write_5m_tokens": int(cache_write_5m_tokens),
                 "cache_write_1h_tokens": int(cache_write_1h_tokens), "cache_read_tokens": int(cache_read_tokens),
                 "estimated_cost_usd": round(cost, 8), "run_id": run_id, "style": style, "note": note}

        def change(data):
            total = float(data["totals_usd"].get(provider, 0.0)) + cost
            data["totals_usd"][provider] = total
            entry["running_total_usd"] = round(total, 8)
            data["entries"].append(entry)

        self._locked(change)
        st = self.status(provider)
        entry.update(warn=st["warn"], exceeded=st["exceeded"], total_usd=st["total_usd"], cap_usd=st["cap_usd"])
        if st["warn"]:
            log.warning("%s spend at %.0f%% of cap after this call", provider, 100 * st["fraction"])
        return entry

    def report(self) -> dict:
        caps = load_caps(self.caps_path)
        return {p: self.status(p) for p in caps.get("providers", {})}


_DEFAULT: Ledger | None = None


def default_ledger() -> Ledger:
    global _DEFAULT
    if _DEFAULT is None:
        _DEFAULT = Ledger()
    return _DEFAULT


def check_budget(provider: str, estimated_cost_usd: float = 0.0, **kw) -> dict:
    return default_ledger().check_budget(provider, estimated_cost_usd, **kw)


def check_and_record(provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0, **kw) -> dict:
    return default_ledger().check_and_record(provider, model, input_tokens, output_tokens, **kw)


def report() -> dict:
    return default_ledger().report()


def stop_cleanly(exc: BudgetExceeded) -> None:
    """Print the stop reason and the resume command, then exit with status 3."""
    print(str(exc), file=sys.stderr)
    print(f"Resume: {exc.resume}", file=sys.stderr)
    raise SystemExit(3)


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
