from __future__ import annotations
import json, re

MEM_TYPES = ("decision", "bugfix", "lesson", "snippet", "preference", "fact")
MAX_QUESTIONS = 3

# GBNF grammar for llama.cpp constrained decoding. Flat object; questions is a
# bounded array of plain strings. llama.cpp enforces structure, not length caps,
# so parse_memories still trims to MAX_QUESTIONS.
GBNF_GRAMMAR = r'''
root   ::= "{" ws "\"memories\"" ws ":" ws "[" ws (mem (ws "," ws mem)*)? ws "]" ws "}"
mem    ::= "{" ws "\"type\"" ws ":" ws type ws "," ws "\"value\"" ws ":" ws string ws "," ws "\"fact\"" ws ":" ws string (ws "," ws "\"questions\"" ws ":" ws qlist)? ws "}"
type   ::= "\"decision\"" | "\"bugfix\"" | "\"lesson\"" | "\"snippet\"" | "\"preference\"" | "\"fact\""
qlist  ::= "[" ws (string (ws "," ws string)*)? ws "]"
string ::= "\"" ( [^"\\] | "\\" ["\\/bfnrt] )* "\""
ws     ::= [ \t\n]*
'''


def build_prompt(session_text: str, *, with_questions: bool) -> str:
    q = ('  "questions": ["<a question a future session might ask that this '
         'memory answers>", ...]  (1-3 items)\n') if with_questions else ""
    q_field = ', "questions":[...]' if with_questions else ""
    return (
        "You are distilling a coding session into reusable memory.\n"
        'Return STRICT JSON: {"memories":[{"type":..., "value":..., "fact":...'
        + q_field + "}]}\n"
        "Fields per memory:\n"
        '  "type": one of decision|bugfix|lesson|snippet|preference|fact\n'
        '  "value": a self-contained, reusable note (1-3 sentences)\n'
        '  "fact": one terse atomic statement of the same memory\n'
        + q +
        "Only durable, reusable facts. Omit chit-chat. Session text:\n---\n"
        + session_text + "\n---"
    )


def _extract_json(raw: str) -> str:
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    return (m.group(1).strip() if m else raw.strip())


def parse_memories(raw: str, *, with_questions: bool) -> list[dict]:
    try:
        data = json.loads(_extract_json(raw))
    except Exception:
        return []
    out: list[dict] = []
    for m in (data.get("memories", []) if isinstance(data, dict) else []):
        if not isinstance(m, dict):
            continue
        mtype = m.get("type")
        value = (m.get("value") or "").strip()
        fact = (m.get("fact") or "").strip()
        if mtype not in MEM_TYPES or not value or not fact:
            continue
        entry = {"type": mtype, "value": value, "fact": fact, "questions": []}
        if with_questions:
            qs = m.get("questions") or []
            entry["questions"] = [q.strip() for q in qs
                                  if isinstance(q, str) and q.strip()][:MAX_QUESTIONS]
        out.append(entry)
    return out
