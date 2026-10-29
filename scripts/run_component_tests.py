"""Run the unit tests associated with the RAG components that changed."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TESTS = ROOT / "tests"

COMPONENT_TESTS = {
    "api": (
        "test_agent",
        "test_api_schemas",
        "test_config",
        "test_dates",
        "test_evals",
        "test_external_tools",
        "test_mcp_registry",
        "test_pipeline",
        "test_search",
        "test_settings",
        "test_tool_catalog",
        "test_transcription",
        "test_voice_stream",
    ),
    "embed": (
        "test_config",
        "test_embed_reconcile",
        "test_embed_watch",
        "test_ingestion_normalized",
        "test_tokenizer_alignment",
    ),
    "normalize": (
        "test_config",
        "test_normalize",
        "test_normalize_reconcile",
        "test_normalize_watch",
    ),
}


def main() -> int:
    components = set(sys.argv[1:])
    unknown = components - COMPONENT_TESTS.keys()
    if unknown:
        print(f"Unknown component(s): {', '.join(sorted(unknown))}", file=sys.stderr)
        return 2
    if not components:
        print("Pass at least one component: api, embed, or normalize", file=sys.stderr)
        return 2

    sys.path[:0] = [str(ROOT), str(TESTS)]
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    modules = sorted({name for component in components for name in COMPONENT_TESTS[component]})
    for module_name in modules:
        suite.addTests(loader.loadTestsFromModule(importlib.import_module(module_name)))

    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
