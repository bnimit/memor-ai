---
name: memorable-recall
description: Recall relevant past coding/research context for a query, scoped to a project, instead of re-deriving it. Use when starting work that resembles past sessions, or when the user asks "what did we decide / how did we fix X".
---

# Memorable Recall

Run: `python skill/recall.py --query "<task or question>" --project "<project>" --db <path> --k 8`

Returns JSON: `context` (a compact block to paste into your working context) and
`trace` (scored hits with component breakdown for inspection). Always pass `--project`
to scope retrieval and minimize tokens. Read the `context`, cite artifact ids when you
use a fact, and prefer recalled decisions over re-deriving them.
