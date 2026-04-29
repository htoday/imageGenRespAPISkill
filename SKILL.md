---
name: "codex-responses-imagegen"
description: "Generate images through an OpenAI-compatible Responses API using streamed image_generation tool calls. Use when each image generation request must explicitly pass base_url and API key instead of reading Codex config, auth storage, or environment defaults."
---

# Codex Responses Image Generation

Use this skill to generate raster images through a standalone OpenAI-compatible Responses API flow. It does not rely on Codex built-in `image_gen`, Codex auth storage, Codex config, or environment-default credentials.

## Core Rule

Every live request must explicitly provide:

- `--base-url`, for example `https://example.com/v1`
- `--api-key`

Do not read API keys from Codex auth storage. Do not read `base_url` from Codex config. Do not fall back to `OPENAI_API_KEY`, `OPENAI_BASE_URL`, or `OPENAI_API_BASE`. If the user has not provided these values for the current request, ask for them before running the script.

Do not print the API key. Do not write it into files. Do not save it in request JSON.

## When To Use

- The user wants image generation through `POST /responses`.
- The user provides or wants to provide a custom OpenAI-compatible gateway.
- The user wants explicit per-request gateway and credential control.
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
  --base-url "https://example.com/v1" \
  --api-key "<api-key>" \
  --prompt "Generate a warm fantasy city illustration..." \
  --out output/image.png
```

`--base-url` and `--api-key` are required for every run. The script intentionally does not auto-read Codex config/auth or environment variables.

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
