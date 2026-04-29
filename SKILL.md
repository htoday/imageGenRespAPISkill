---
name: "codex-responses-imagegen"
description: "Generate images through an OpenAI-compatible Responses API using streamed image_generation tool calls. Use when image generation should prefer local Codex config/auth, then fall back to explicit base_url and API key prompts only when local values are unavailable."
---

# Codex Responses Image Generation

Use this skill to generate raster images through a standalone OpenAI-compatible Responses API flow. It does not rely on Codex built-in `image_gen`, but it should prefer local Codex config/auth when available.

## Core Rule

Before asking the user for connection details, try local Codex values first:

1. Read `$CODEX_HOME/config.toml`, or `~/.codex/config.toml` if `CODEX_HOME` is unset.
2. Use the selected `model_provider`'s `base_url` from `[model_providers.<name>]`.
3. If the selected provider is unavailable, try `[model_providers.OpenAI]`, `[model_providers.openai]`, then `[model_providers.cch]`.
4. Read `$CODEX_HOME/auth.json`, or `~/.codex/auth.json` if `CODEX_HOME` is unset.
5. Use an API key from `OPENAI_API_KEY`, `api_key`, `token`, or `access_token` fields when present.
6. If local Codex values are missing, fall back to `OPENAI_BASE_URL` / `OPENAI_API_BASE` and `OPENAI_API_KEY`.
7. Only if values are still missing, ask the user for `base_url` and API key.

Do not print the API key. Do not write it into files. If a key must be requested, use an interactive hidden prompt.

## When To Use

- The user wants image generation through `POST /responses`.
- The user provides or wants to provide a custom OpenAI-compatible gateway.
- The user wants to use the local Codex provider settings without manually copying the key.
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
  --prompt "Generate a warm fantasy city illustration..." \
  --out output/image.png
```

`--base-url` and `--api-key` are optional overrides. If omitted, the script reads local Codex config/auth first, then environment variables, then prompts interactively only when needed.

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
