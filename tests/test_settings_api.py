"""HTTP settings contract, with a disposable profile and host-owned settings writers."""

import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

from test_offline import ROOT
from bg_law_plugin import settings, client

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:
    FastAPI = None


@unittest.skipIf(FastAPI is None, "Optional FastAPI/httpx host integration dependencies are unavailable")
class SettingsApiTests(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        self.saved, self.secrets = {}, {}
        config = types.ModuleType("hermes_cli.config")
        config.load_config_readonly = lambda: {"plugins": {"entries": {"bg-law": {"settings": self.saved}}}}
        config.get_env_value = lambda name: self.secrets.get(name)
        config.save_env_value = lambda name, value: self.secrets.update({name: value})
        def set_config_value(path, value):
            name = path.rsplit(".", 1)[1]
            try:
                self.saved[name] = json.loads(value)
            except ValueError:
                self.saved[name] = value
        config.set_config_value = set_config_value
        state = types.ModuleType("hermes_cli.plugins_state")
        def save(plugin, segments, value):
            self.assertEqual(plugin, "bg-law")
            self.assertIsInstance(segments, tuple)
            self.saved[segments[0]] = value
        state.save_plugin_setting = save
        class Context:
            def set_config(_, name, value):
                save("bg-law", (name,), value)
        self.context = mock.patch.object(settings, "_CONTEXT", Context())
        self.context.start()
        self.host = mock.patch.dict(sys.modules, {"hermes_cli": types.ModuleType("hermes_cli"),
                                                  "hermes_cli.config": config, "hermes_cli.plugins_state": state})
        self.host.start()
        self.paths = [mock.patch.object(settings, "hermes_home", return_value=self.home),
                      mock.patch.object(client, "hermes_home", return_value=self.home)]
        for patch in self.paths:
            patch.start()
        spec = importlib.util.spec_from_file_location("settings_api_test", ROOT / "dashboard/plugin_api.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module._settings = lambda: settings
        app = FastAPI()
        app.include_router(module.router)
        self.api = TestClient(app)

    def tearDown(self):
        self.host.stop()
        self.context.stop()
        for patch in self.paths:
            patch.stop()

    def test_save_credential_and_preserve_blank(self):
        response = self.api.post("/config", json={"settings": {"language": "en"}, "api_key": "sk_PRIVATE"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.secrets["LSOURCE_API_KEY"], "sk_PRIVATE")
        self.assertNotIn("sk_PRIVATE", response.text)
        self.api.post("/config", json={"api_key": ""})
        self.assertEqual(self.secrets["LSOURCE_API_KEY"], "sk_PRIVATE")
        self.assertNotIn("sk_PRIVATE", self.api.get("/diagnostics").text)

    def test_invalid_settings_do_not_write(self):
        response = self.api.post("/config", json={"settings": {"max_age_hours": -1}, "api_key": "secret"})
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.saved, {})
        self.assertEqual(self.secrets, {})

    def test_settings_save_before_agent_registration(self):
        with mock.patch.object(settings, "_CONTEXT", None):
            response = self.api.post("/config", json={"settings": {"language": "en", "max_age_hours": 12}, "api_key": "sk_test"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.saved["language"], "en")
        self.assertEqual(self.saved["max_age_hours"], 12)

    def test_dashboard_discovery_manifest(self):
        manifest = json.loads((ROOT / "dashboard/manifest.json").read_text())
        self.assertEqual(manifest["name"], "bg-law")
        self.assertTrue((ROOT / "dashboard" / manifest["api"]).is_file())

    def test_legacy_case_switch_migrates(self):
        self.saved["case_law"] = False
        initial = self.api.get("/status").json()
        self.assertFalse(initial["settings"]["bg_cases"])
        self.assertFalse(initial["settings"]["eu_cases"])
        result = self.api.post("/config", json={"settings": {"bg_cases": True, "eu_cases": False}}).json()
        self.assertTrue(result["settings"]["bg_cases"])
        self.assertFalse(result["settings"]["eu_cases"])

    def test_cache_expiry_preserves_offline_copy(self):
        cache = client.Cache()
        cache.write("sample", {"public": "text"}, {"fetched_at": 123, "used_at": 123})
        # Recheck does not need to run real source probes in this integration test.
        with mock.patch.object(settings, "probe", side_effect=lambda: settings.status()):
            self.assertEqual(self.api.post("/cache/recheck").status_code, 200)
        data, meta = cache.read("sample")
        self.assertEqual(meta["fetched_at"], 0)
        self.assertEqual(data, {"public": "text"})
        self.assertEqual(self.api.post("/cache/clear").json()["cache"]["entries"], 0)
