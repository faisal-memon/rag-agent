"""Version metadata exposed by the API and web interface."""

import os


APP_VERSION = os.getenv("RAG_AGENT_VERSION", "dev")
