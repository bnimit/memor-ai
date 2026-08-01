# Proxy Benchmark Fixtures

This directory contains fixture request bodies for benchmarking the proxy compression pipeline.

## Fixture Format

Each `.json` file has the following structure:

```json
{
  "name": "fixture_name",
  "provider": "anthropic" | "openai",
  "tool_rich": true | false,
  "required_substrings": ["substring1", "substring2"],
  "body": { ... Anthropic or OpenAI request body ... }
}
```

## Fields

- `name`: Human-readable fixture name
- `provider`: API provider (anthropic or openai)
- `tool_rich`: Whether this fixture contains tool-heavy logs/JSON (true) or simple text (false)
- `required_substrings`: List of strings that MUST appear in the compressed output (correctness check)
- `body`: The actual API request body to run through the pipeline

## Fixtures

1. **01_error_logs.json** (tool-rich): Anthropic error logs with traceback
2. **02_json_response.json** (tool-rich): Anthropic JSON API response with user data
3. **03_openai_build_logs.json** (tool-rich): OpenAI build logs with webpack errors
4. **04_simple_text.json** (not tool-rich): Anthropic simple text response
5. **05_api_response_logs.json** (tool-rich): OpenAI API gateway logs with timeouts
6. **06_openai_simple.json** (not tool-rich): OpenAI simple tool result

## Release Gate

The benchmark harness uses these fixtures to verify:
- Mean token reduction ≥15% on tool-rich subset (fixtures 1, 2, 3, 5)
- At least 3 tool-rich fixtures with pct_saved > 0
- All required_substrings preserved in compressed output
