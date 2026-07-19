# Agent Runtime

`POST /agent/query` runs a small, bounded controller loop. The model proposes
one next action at a time; Python validates it, executes the action, and gives
the resulting evidence back to the model for the next decision.

```text
browser request + recent chat history
              |
              v
service.answer_with_agent
              |
              +--> read saved memory (routing guidance, not proof)
              |
              +--> planner prompt --> LLM text
              |                         |
              |                         v
              |                   protocol.py
              |                   parse + allowlist + bound arguments
              |                         |
              |                         v
              +--> tools.py --> Postgres / normalized Markdown / memory file
              |
              +--> next planning turn, or answer prompt --> cited reply
```

## Responsibilities

- `service.py` is the controller. It owns the loop limit, duplicate-call
  protection, the absence-search rule, prompt rendering, model calls, and the
  response trace.
- `protocol.py` treats model output as untrusted input. It supports the JSON
  planner format and the native llama.cpp tool-call format, then allowlists
  tool names and bounds their arguments.
- `tools.py` owns the document capabilities: discovery, PostgreSQL keyword and
  semantic search, literal Markdown grep, and bounded document reads. It also
  exposes memory read/write functions in the tool registry.
- `memory.py` owns the memory file, Markdown normalization, approval detection,
  proposed-memory extraction, and prompt-safe memory rendering.
- `prompts/` holds the human-readable instructions for the planner, final
  answer, and shared system role.

## A Concrete Turn

For “What car do I have?”, the planner may choose `search_documents` first to
look for likely vehicle paths. If that is empty or weak, it should choose
`semantic_search` or `keyword_search` next. A strong chunk can lead to
`read_document` for surrounding evidence, followed by an answer. Every tool
call and result is retained in the response trace, and the browser renders
that trace in chronological order.

The model cannot directly read arbitrary files, query arbitrary SQL, or write
memory. It can only request one of the declared tools, and `protocol.py`
rejects unknown tools and clamps numeric limits before `service.py` executes
anything.
