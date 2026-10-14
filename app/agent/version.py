"""Version metadata exposed by the API and web interface."""

try:
    from app.agent._build_version import APP_VERSION
except ModuleNotFoundError:
    # Source checkouts have no generated build metadata.
    APP_VERSION = "dev"
