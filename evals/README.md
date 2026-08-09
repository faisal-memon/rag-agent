# Agent evaluations

This directory contains deterministic checks for a running RAG agent.  Cases are
JSON objects in a list.  Keep local document-specific questions and expected paths
in `cases.local.json`, which is ignored by Git.

Each case requires `name` and `question`.  The optional checks are:

- `required_answer_substrings` and `forbidden_answer_substrings`
- `required_citation_path_substrings` and `forbidden_citation_path_substrings`
- `minimum_tool_calls` and `maximum_tool_calls`
- `required_tools`
- `minimum_distinct_retrieval_tools` (counts `semantic_search` and `keyword_search`)

Start the agent, create a local cases file once, edit it for the indexed documents,
then run the evaluations:

```sh
make eval-init
make eval
```

`make eval-init` will not overwrite an existing local file.  To target a different
server, set `RAG_EVAL_BASE_URL`:

```sh
RAG_EVAL_BASE_URL=http://localhost:8001 make eval
```

You can also run a selected case file directly:

```sh
.venv/bin/python -m app.agent.evals evals/cases.example.json
```
