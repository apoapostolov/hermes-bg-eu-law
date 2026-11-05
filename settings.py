"""Profile-scoped settings and redacted source diagnostics shared by tools and UI."""

import json
import time
from .client import Http, LawError, Store, _atomic_write, _status_error, hermes_home
from .version import __version__

DEFAULTS = {"language": "auto", "eu_language": "bg", "max_age_hours": 24,
            "cache_max_mb": 200, "allow_stale": True, "bg_law": True,
            "eu_law": True, "case_law": True, "bg_cases": True, "eu_cases": True}
_HTTP = Http()
_CONTEXT = None


def bind_context(context):
    global _CONTEXT
    _CONTEXT = context


def read_settings():
    try:
        from hermes_cli.config import load_config_readonly
        cfg = load_config_readonly() or {}
        saved = cfg.get("plugins", {}).get("entries", {}).get("bg-law", {}).get("settings", {})
    except ImportError:
        saved = {}
    values = {**DEFAULTS, **{k: v for k, v in saved.items() if k in DEFAULTS}}
    if not values["case_law"]:
        values.update(bg_cases=False, eu_cases=False)
    return values


def api_key():
    try:
        from hermes_cli.config import get_env_value
        return get_env_value("LSOURCE_API_KEY") or ""
    except ImportError:
        import os
        return os.environ.get("LSOURCE_API_KEY", "")


def runtime_store():
    def setting(key, default=None):
        return api_key() if key == "lsource_api_key" else read_settings().get(key, default)
    return Store(http=_HTTP, settings=setting)


def validate(values):
    if set(values) - set(DEFAULTS):
        raise LawError("bad_argument", "Unknown setting name.")
    for key, value in values.items():
        if isinstance(DEFAULTS[key], bool) and not isinstance(value, bool):
            raise LawError("bad_argument", f"{key} must be true or false.")
        if key in ("max_age_hours", "cache_max_mb"):
            lo, hi = (0, 8760) if key == "max_age_hours" else (5, 10000)
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                label = "Recheck interval" if key == "max_age_hours" else "Cache target"
                raise LawError("bad_argument", f"{label} must be a whole number from {lo} to {hi}.")
        if key == "language" and value not in ("auto", "bg", "en"):
            raise LawError("bad_argument", "Attribution language must be auto, bg or en.")
        if key == "eu_language" and value not in ("bg", "en"):
            raise LawError("bad_argument", "EU text language must be bg or en.")
    return values


def save_settings(values, key=None):
    validate(values)
    if key is not None and (not isinstance(key, str) or any(c.isspace() for c in key) or len(key) > 512):
        raise LawError("bad_argument", "The API key must be one token without whitespace.")
    from hermes_cli.config import save_env_value, set_config_value
    values = dict(values)
    if "bg_cases" in values or "eu_cases" in values:
        values = {"case_law": True, **{k: v for k, v in values.items() if k != "case_law"}}
    for name, value in values.items():
        if _CONTEXT is not None:
            _CONTEXT.set_config(name, value)
        else:
            # A dashboard host can start before any agent register(ctx) call.
            # Use the public CLI configuration API in that process as well.
            wire = str(value).lower() if isinstance(value, bool) else str(value)
            try:
                set_config_value(f"plugins.entries.bg-law.settings.{name}", wire)
            except SystemExit as err:
                raise LawError("settings_write_failed", "Hermes refused the settings update.") from err
            if read_settings().get(name) != value:
                raise LawError("settings_write_failed", "Hermes did not save the requested setting.",
                               "Check whether the profile or setting is administrator-managed.")
    if key:  # blank means keep the current credential
        save_env_value("LSOURCE_API_KEY", key)
        diagnostics_path().unlink(missing_ok=True)


def diagnostics_path():
    path = hermes_home() / "plugin-data" / "bg-law" / "diagnostics.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def status(store=None):
    store = store or runtime_store()
    try:
        checks = json.loads(diagnostics_path().read_text(encoding="utf-8"))
    except (ValueError, OSError):
        checks = {}
    return {"version": __version__, "key_stored": bool(store.setting("lsource_api_key", "")),
            "settings": {k: store.setting(k, v) for k, v in DEFAULTS.items()},
            "cache": {"bytes": store.cache.size_bytes(), "entries": len(list(store.cache.root.glob("*.json")))},
            "sources": checks, "setup_url": "https://lsource.net/developers"}


def probe(store=None):
    store = store or runtime_store()
    previous = status(store).get("sources", {})
    checks = {}
    targets = [("bg", "bg_law", "https://lsource.net/v1/acts?limit=1"),
               ("eu", "eu_law", "https://publications.europa.eu/webapi/rdf/sparql?query=ASK%7B%7D&format=application%2Fsparql-results%2Bjson"),
               ("court", "bg_cases", "https://www.constcourt.bg/bg/search-acts")]
    for name, flag, url in targets:
        checked = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        item = {"checked_at": checked}
        if previous.get(name, {}).get("last_success_at"):
            item["last_success_at"] = previous[name]["last_success_at"]
        try:
            enabled = store.setting(flag, True)
            if name == "eu":
                enabled = enabled or (store.setting("case_law", True) and store.setting("eu_cases", True))
            if name == "court":
                enabled = enabled and store.setting("case_law", True)
            if not enabled:
                item["state"] = "disabled"
            elif name == "bg" and not store.setting("lsource_api_key", ""):
                item["state"] = "key_required"
            else:
                headers = {"Authorization": "Bearer " + store.setting("lsource_api_key", "")} if name == "bg" else {}
                response = store.http.get(url, headers)
                if response.status != 200:
                    raise _status_error(response)
                try:
                    if name != "court":
                        body = json.loads(response.body)
                        if not isinstance(body, dict) or (name == "bg" and "results" not in body) or (name == "eu" and "boolean" not in body):
                            raise ValueError()
                    elif b"result-item" not in response.body:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise LawError("bad_response", "The source did not return its expected public API response.")
                item["state"] = "connected"
                item["last_success_at"] = checked
        except LawError as err:
            item.update(state=err.kind, next_step=err.hint or "Check the source and retry later.")
        checks[name] = item
    _atomic_write(diagnostics_path(), json.dumps(checks).encode())
    return status(store)


def clear_cache(store=None):
    cache = (store or runtime_store()).cache
    for path in cache.root.iterdir():
        if path.is_file() and path.suffix in (".json", ".meta"):
            path.unlink(missing_ok=True)
    cache._memo.clear()
    return status(store)


def expire_cache(store=None):
    cache = (store or runtime_store()).cache
    for path in cache.root.glob("*.meta"):
        try:
            meta = json.loads(path.read_text(encoding="utf-8"))
            meta["fetched_at"] = 0
            _atomic_write(path, json.dumps(meta).encode())
        except (OSError, ValueError):
            continue
    return status(store)
