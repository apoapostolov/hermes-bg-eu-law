"""Authenticated Hermes plugin routes for the bg-law settings page."""

import hashlib
import importlib.util
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()


def _settings():
    root = Path(__file__).resolve().parents[1]
    # Reuse the plugin's own registered context. The dashboard importer may use
    # a different module name, so match our entry file rather than a core registry.
    for module in tuple(sys.modules.values()):
        filename = getattr(module, "__file__", None)
        if filename and Path(filename).resolve() == root / "__init__.py":
            bound = getattr(module, "_PLUGIN_SETTINGS", None)
            if bound is not None:
                return bound
    name = "bg_law_dashboard_" + hashlib.sha256(str(root).encode()).hexdigest()[:12]
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, root / "__init__.py", submodule_search_locations=[str(root)])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    from importlib import import_module
    return import_module(name + ".settings")


class ConfigPayload(BaseModel):
    settings: dict = Field(default_factory=dict)
    api_key: str | None = None


def _call(method, *args):
    module = _settings()
    try:
        return getattr(module, method)(*args)
    except module.LawError as err:
        raise HTTPException(status_code=400, detail={"error_type": err.kind, "message": err.message,
                                                   "next_step": err.hint}) from err
    except PermissionError as err:
        raise HTTPException(status_code=403, detail="These settings are administrator-managed.") from err
    except ImportError as err:
        raise HTTPException(status_code=503, detail="Settings writes require the Hermes host.") from err


@router.get("/status")
def status():
    return _call("status")


@router.post("/config")
def config(payload: ConfigPayload):
    _call("save_settings", payload.settings, payload.api_key)
    return _call("status")


@router.post("/check")
def check():
    return _call("probe")


@router.post("/cache/recheck")
def recheck():
    _call("expire_cache")
    return _call("probe")


@router.post("/cache/clear")
def clear():
    return _call("clear_cache")


@router.get("/diagnostics")
def diagnostics():
    return _call("status")  # booleans, public source checks and settings; never credentials or paths
