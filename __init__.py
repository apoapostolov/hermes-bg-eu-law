"""bg-law: Bulgarian and EU law for Hermes Agent.

Read-only lookups against lsource.net (Bulgarian consolidated legislation,
reconstructed from the State Gazette), the Publications Office of the European
Union (CELLAR) and the Bulgarian Constitutional Court. Texts are cached under
<HERMES_HOME>/plugin-data/bg-law/cache/. The plugin returns legal texts, not
legal advice.
"""

import logging
import os
from pathlib import Path

from . import schemas
from .client import Store
from .tools import Tools, make_handlers

logger = logging.getLogger(__name__)

_SKILL = Path(__file__).resolve().parent / "skills" / "read-law" / "SKILL.md"
_SKILL_DESCRIPTION = (
    "Bulgarian or EU law question (чл., КТ, НК, ЗЗД, ОРЗД/GDPR, AI Act, КС, СЕС)? Before web_search, "
    "use tool_search for the bg-law tools and quote source.attribution."
)


def register(ctx):
    """Register the six read-only tools, then the quoting skill."""

    def setting(key, default=None):
        try:
            value = ctx.get_config(key, default)
        except Exception:
            value = default
        if value not in (None, ""):
            return value
        if key == "lsource_api_key":
            try:
                from agent.secret_scope import get_secret  # host-owned secret store (.env)
                value = get_secret("LSOURCE_API_KEY")
            except Exception:
                value = None
            if value:
                return value
            return os.environ.get("LSOURCE_API_KEY", "") or default
        return default

    handlers = make_handlers(Tools(Store(settings=setting)))
    for schema in schemas.ALL_SCHEMAS:
        ctx.register_tool(
            name=schema["name"],
            toolset="bg_law",
            schema=schema,
            handler=handlers[schema["name"]],
            emoji="⚖️",
        )
    try:
        ctx.register_skill("read-law", _SKILL, description=_SKILL_DESCRIPTION)
    except Exception:
        logger.warning("bg-law skill was not registered", exc_info=True)
