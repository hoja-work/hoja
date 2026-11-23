"""Hoja GraphQL schema text, exposed to the agent so it can discover fields."""

from pathlib import Path

from hoja_server.agent_members import SDL as AGENT_MEMBERS_SDL
from hoja_server.agent_conversations import SDL as CONVERSATION_SDL
from hoja_server.knowledge import SDL as KNOWLEDGE_SDL
from hoja_server.workspace_templates import SDL as WORKSPACE_TEMPLATES_SDL
from hoja_server.decisions_api import SDL as DECISIONS_SDL
from hoja_server.model_providers import SDL as PROVIDER_SDL
from hoja_server.permissions import SDL as PERMISSIONS_SDL
from hoja_server.project_settings import SDL as PROJECT_SETTINGS_SDL
from hoja_server.sla import SDL as SLA_SDL
from hoja_server.task_templates import SDL as TASK_TEMPLATES_SDL
from hoja_server.profile_metrics import SDL as PROFILE_METRICS_SDL
from hoja_server.usage_ledger import SDL as USAGE_LEDGER_SDL

SCHEMA_SDL = (Path(__file__).parent / "schema.graphql").read_text() + "\n" + PROVIDER_SDL + "\n" + CONVERSATION_SDL + "\n" + DECISIONS_SDL + "\n" + PROJECT_SETTINGS_SDL + "\n" + PERMISSIONS_SDL + "\n" + TASK_TEMPLATES_SDL + "\n" + SLA_SDL + "\n" + AGENT_MEMBERS_SDL + "\n" + USAGE_LEDGER_SDL + "\n" + PROFILE_METRICS_SDL + "\n" + KNOWLEDGE_SDL + "\n" + WORKSPACE_TEMPLATES_SDL
