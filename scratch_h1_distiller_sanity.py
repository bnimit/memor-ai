"""H1 local-distiller sanity check. Run the CURRENT terse distill prompt and the
ENRICHED (A-MEM-style: keywords/tags/context + self-contained text) prompt on a
few real sessions with qwen-14b, and print both so we can eyeball whether the
local model produces usable enrichment before trusting any metric.
"""
import os, json, re
from memor.store.sqlite_store import SqliteStore
from memor.embed.local import LocalEmbedder
from memor.llm.openai_compat import OpenAICompatLLM

DB = os.path.expanduser("~/.memor/memor.db")
e = LocalEmbedder()
s = SqliteStore(DB, dim=e.dim)
llm = OpenAICompatLLM("http://192.168.1.6:1234/v1", "lmstudio",
                      "qwen2.5-14b-instruct-mlx", temperature=0)

CURRENT = """You are distilling a coding session into reusable memory.
Return STRICT JSON: {{"memories":[{{"type":"decision|lesson|snippet|bugfix",
"text":"<one concise reusable fact>", "supersedes_text":"<prior fact this reverses, or omit>"}}]}}
Only include durable, reusable facts. Session text:
---
{session_text}
---"""

ENRICHED = """You are distilling a coding session into reusable memory for a future coding agent.
Return STRICT JSON: {{"memories":[{{
  "type":"decision|lesson|snippet|bugfix",
  "text":"<one concise, SELF-CONTAINED reusable fact — resolve pronouns/implicit subjects so it stands alone without the session>",
  "keywords":["<exact identifiers, file names, error strings, API/tool names a future query might use>"],
  "tags":["<2-5 short topic tags>"],
  "context":"<one sentence: in what situation a future agent would want this>",
  "supersedes_text":"<prior fact this reverses, or omit>"}}]}}
Only durable, reusable facts. Session text:
---
{session_text}
---"""


def extract_json(raw):
    m = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", raw, re.DOTALL)
    return json.loads(m.group(1).strip() if m else raw.strip())


# pick a few real sessions with enough chunks
rows = s.db.execute("""SELECT meta, text, project, json_extract(meta,'$.session_id') sid,
  json_extract(meta,'$.ord') ord FROM artifacts WHERE kind='session_chunk' AND active=1""").fetchall()
by_sess = {}
for r in rows:
    by_sess.setdefault((r["project"], r["sid"]), []).append((r["ord"] or 0, r["text"]))
sized = sorted(((k, v) for k, v in by_sess.items() if 6 <= len(v) <= 40),
               key=lambda kv: len(kv[1]))
sample = sized[len(sized)//2: len(sized)//2 + 3]  # 3 mid-sized sessions

for (project, sid), chunks in sample:
    chunks.sort()
    txt = "\n".join(t for _, t in chunks)[:6000]
    print("\n" + "=" * 90)
    print(f"PROJECT={project}  SESSION={str(sid)[:12]}  ({len(chunks)} chunks)")
    for label, prompt in [("CURRENT (terse)", CURRENT), ("ENRICHED (H1)", ENRICHED)]:
        print(f"\n----- {label} -----")
        try:
            out = extract_json(llm.complete(prompt.format(session_text=txt), max_tokens=1500))
            for mem in out.get("memories", []):
                if label.startswith("CURRENT"):
                    print(f"  [{mem.get('type')}] {mem.get('text')}")
                else:
                    print(f"  [{mem.get('type')}] {mem.get('text')}")
                    print(f"     keywords: {mem.get('keywords')}")
                    print(f"     tags:     {mem.get('tags')}")
                    print(f"     context:  {mem.get('context')}")
                    emb_text = " ".join([str(mem.get('text','')), " ".join(mem.get('keywords',[])),
                                         " ".join(mem.get('tags',[])), str(mem.get('context',''))])
                    print(f"     -> embed text: {emb_text[:160]}...")
        except Exception as ex:
            print(f"  PARSE/LLM ERROR: {type(ex).__name__}: {ex}")
