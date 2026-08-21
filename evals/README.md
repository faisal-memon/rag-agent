# Agent evaluations

This directory contains simple deterministic checks for a running RAG agent. Cases
are JSON objects in a list. Keep local document-specific questions and expected
answers in `cases.local.json`, which is ignored by Git.

Each case has `question` and `expected_answer_substrings`. The latter is an array
of acceptable answer variants. The case passes when the agent's `answer` contains
at least one expected string, without regard to letter case.

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
