---
name: "codex-responses-imagegen"
description: "Generate images through an OpenAI-compatible Responses API using streamed image_generation tool calls. Use when the AI should discover usable base_url and API key from .codex and environment variables, ask the user if none or multiple are found, then call the script with explicit parameters."
---

# Codex Responses Image Generation

Use this skill to generate raster images through a standalone OpenAI-compatible Responses API flow. The script itself only accepts explicit credentials, while the AI using this skill is responsible for discovering or asking for them first.

## Core Rule

Before running the script, the AI must resolve connection details in this order:

1. Check values the user explicitly supplied in the current request.
2. Read `$CODEX_HOME/config.toml` or `~/.codex/config.toml` for model provider `base_url` values.
3. Read `$CODEX_HOME/auth.json` or `~/.codex/auth.json` for API key values.
4. Check `OPENAI_BASE_URL`, `OPENAI_API_BASE`, and `OPENAI_API_KEY` environment variables.

Selection rules:

- If no usable `base_url` or API key is found, ask the user for the missing value.
- If multiple usable `base_url` or API key candidates are found, ask the user which one to use.
- If exactly one candidate is found for each value, print the selected base URL and API key in masked form before calling the script.
- Always call `scripts/stream_responses_image.py` with explicit `--base-url` and `--api-key`.

Never print a raw API key. Never write the API key into files. Do not save it in request JSON.

AI-side discovery can use shell or a small one-off local script to inspect:

- model provider `base_url` entries in `.codex/config.toml`
- API key fields in `.codex/auth.json`
- `OPENAI_BASE_URL`, `OPENAI_API_BASE`, and `OPENAI_API_KEY`

When reporting discovery results to the user, mask API keys, for example `sk-a...1234`.

## When To Use

- The user wants image generation through `POST /responses`.
- The user provides or wants to provide a custom OpenAI-compatible gateway.
- The user wants automatic discovery from `.codex` and environment variables.
- The normal Images API path (`/images/generations`) fails, but Responses image tools may work.
- The task needs streamed SSE handling and base64 result decoding.

## When Not To Use

- The user wants Codex's built-in `image_gen` tool.
- The task is better solved with SVG, HTML/CSS, canvas, or deterministic code-native assets.

## Proven Request Pattern

Use a streamed Responses request:

```json
{
  "model": "gpt-5.5",
  "input": "<detailed prompt>",
  "stream": true,
  "tools": [
    { "type": "image_generation", "model": "gpt-image-2" }
  ]
}
```

Important observed behavior:

- `POST /images/generations` may return `503 no_available_providers` even when Responses image generation works.
- `gpt-5.4` may create a response without producing an `image_generation_call` on some gateways.
- `gpt-5.5` with `{"type":"image_generation","model":"gpt-image-2"}` successfully produced a PNG from `response.output_item.done`.

Default to outer model `gpt-5.5` unless the user requests another model or the gateway does not expose it.

## Streaming Rules

Treat the response as Server-Sent Events.

1. Read response lines.
2. Process only lines beginning with `data:`.
3. Ignore `[DONE]`, blank payloads, and non-JSON payloads.
4. Watch for `response.output_item.done`.
5. Inspect `event["item"]`.
6. When `item.type == "image_generation_call"` and `item.result` exists, decode `item.result` from base64.
7. Save the decoded bytes as `.png` or `.jpg`.

Do not rely on the final JSON response body for the image payload.

## Script

Use the bundled script:

```bash
python scripts/stream_responses_image.py \
  --base-url "<resolved-base-url>" \
  --api-key "<resolved-api-key>" \
  --prompt "Generate a warm fantasy city illustration..." \
  --out output/image.png
```

`--base-url` and `--api-key` are required script arguments. The AI should discover or ask for values first, then pass them explicitly.

For detailed CLI examples and troubleshooting, read `references/usage.md`.

## Prompting

Use concrete visual prompts. Include:

- subject and setting
- named characters or objects
- composition and aspect ratio
- style and medium
- lighting and mood
- constraints and avoid list

For copyrighted or game-inspired fan-art style requests, describe the concrete scene and characters the user requested. Avoid adding unrelated characters or logos.

## Output

Report:

- saved artifact path
- outer Responses model
- image tool model
- short note that the result came from streamed `image_generation_call.result`
