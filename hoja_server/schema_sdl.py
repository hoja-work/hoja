"""Hoja GraphQL schema text, exposed to the agent so it can discover fields."""

from pathlib import Path

from hoja_server.agent_conversations import SDL as CONVERSATION_SDL
from hoja_server.model_providers import SDL as PROVIDER_SDL

SCHEMA_SDL = (Path(__file__).parent / "schema.graphql").read_text() + "\n" + PROVIDER_SDL + "\n" + CONVERSATION_SDL
