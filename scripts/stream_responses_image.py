#!/usr/bin/env python3
"""Generate an image through streamed Responses image_generation calls.

This script is intentionally dependency-free. It uses urllib so it can run
outside Codex without installing the OpenAI SDK.
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
from pathlib import Path
import socket
import sys
import tomllib
import urllib.error
import urllib.request


def _codex_home() -> Path:
    return Path(os.getenv("CODEX_HOME", Path.home() / ".codex")).expanduser()


def _normalize_base_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit("base_url is required")
    return value.rstrip("/")


def _read_codex_config() -> dict:
    path = _codex_home() / "config.toml"
    if not path.exists():
        return {}
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
        return {}


def _read_codex_auth() -> dict:
    path = _codex_home() / "auth.json"
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"Warning: could not parse {path}: {exc}", file=sys.stderr)
        return {}


def _get_base_url(args: argparse.Namespace, codex_config: dict) -> str:
    if args.base_url:
        return _normalize_base_url(args.base_url)

    provider_name = codex_config.get("model_provider")
    providers = codex_config.get("model_providers") or {}
    candidates = []
    if provider_name:
        candidates.append(provider_name)
    candidates.extend(["OpenAI", "openai", "cch"])

    for name in candidates:
        provider = providers.get(name)
        if isinstance(provider, dict) and provider.get("base_url"):
            return _normalize_base_url(str(provider["base_url"]))

    env_url = os.getenv("OPENAI_BASE_URL") or os.getenv("OPENAI_API_BASE")
    if env_url:
        return _normalize_base_url(env_url)

    entered = input("Base URL: ").strip()
    if not entered:
        raise SystemExit("base_url is required.")
    return _normalize_base_url(entered)


def _read_prompt(args: argparse.Namespace) -> str:
    if args.prompt and args.prompt_file:
        raise SystemExit("Use either --prompt or --prompt-file, not both.")
    if args.prompt_file:
        return Path(args.prompt_file).read_text(encoding="utf-8")
    if args.prompt:
        return args.prompt
    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return text
    raise SystemExit("Provide --prompt, --prompt-file, or prompt text on stdin.")


def _get_api_key(args: argparse.Namespace, codex_auth: dict) -> str:
    if args.api_key:
        return args.api_key

    for key_name in ("OPENAI_API_KEY", "api_key", "token", "access_token"):
        value = codex_auth.get(key_name)
        if isinstance(value, str) and value.strip():
            return value.strip()

    tokens = codex_auth.get("tokens")
    if isinstance(tokens, dict):
        for key_name in ("OPENAI_API_KEY", "api_key", "token", "access_token"):
            value = tokens.get(key_name)
            if isinstance(value, str) and value.strip():
                return value.strip()

    env_key = os.getenv("OPENAI_API_KEY")
    if env_key:
        return env_key
    key = getpass.getpass("API key: ").strip()
    if not key:
        raise SystemExit("API key is required.")
    return key


def _build_payload(args: argparse.Namespace, prompt: str) -> dict:
    tool: dict[str, object] = {
        "type": "image_generation",
        "model": args.image_model,
    }
    if args.tool_action:
        tool["action"] = args.tool_action

    return {
        "model": args.model,
        "input": prompt,
        "stream": True,
        "tools": [tool],
    }


def _save_request_json(path: str | None, payload: dict) -> None:
    if not path:
        return
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _event_payloads(response) -> object:
    for raw in response:
        line = raw.decode("utf-8", errors="replace").strip()
        if not line.startswith("data:"):
            continue
        data = line[5:].strip()
        if not data or data == "[DONE]":
            continue
        try:
            yield json.loads(data)
        except json.JSONDecodeError:
            continue


def _post_stream(args: argparse.Namespace, base_url: str, api_key: str, payload: dict) -> int:
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    req = urllib.request.Request(
        f"{base_url}/responses",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    image_count = 0
    response_id = None
    event_counts: dict[str, int] = {}

    try:
        with urllib.request.urlopen(req, timeout=args.timeout) as response:
            for event in _event_payloads(response):
                event_type = event.get("type") or "unknown"
                event_counts[event_type] = event_counts.get(event_type, 0) + 1

                if event_type in {
                    "response.created",
                    "response.completed",
                    "response.failed",
                    "response.incomplete",
                }:
                    response_obj = event.get("response") or {}
                    response_id = response_id or response_obj.get("id")
                    print(
                        json.dumps(
                            {
                                "event": event_type,
                                "response_id": response_obj.get("id"),
                                "status": response_obj.get("status"),
                                "error": response_obj.get("error"),
                            },
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )

                if event_type != "response.output_item.done":
                    continue

                item = event.get("item") or {}
                print(
                    json.dumps(
                        {
                            "event": event_type,
                            "item_type": item.get("type"),
                            "status": item.get("status"),
                        },
                        ensure_ascii=False,
                    ),
                    flush=True,
                )
                if item.get("type") == "image_generation_call" and item.get("result"):
                    image_count += 1
                    target = out if image_count == 1 else out.with_name(
                        f"{out.stem}-{image_count}{out.suffix}"
                    )
                    target.write_bytes(base64.b64decode(item["result"]))
                    print(
                        json.dumps(
                            {"saved": str(target), "bytes": target.stat().st_size},
                            ensure_ascii=False,
                        ),
                        flush=True,
                    )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        print(
            json.dumps(
                {"http_status": exc.code, "reason": exc.reason, "body": body},
                ensure_ascii=False,
            ),
            file=sys.stderr,
        )
        return 1
    except urllib.error.URLError as exc:
        print(
            json.dumps({"network_error": str(exc.reason)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1
    except TimeoutError as exc:
        print(json.dumps({"timeout": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    except socket.timeout as exc:
        print(json.dumps({"timeout": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "response_id": response_id,
                "image_count": image_count,
                "events": event_counts,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    if image_count == 0:
        print("No image_generation_call result found in stream.", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Generate an image through streamed Responses image_generation calls."
    )
    parser.add_argument("--base-url", help="OpenAI-compatible base URL. Defaults to Codex config, then OPENAI_BASE_URL, then prompt.")
    parser.add_argument("--api-key", help="API key. Defaults to Codex auth, then OPENAI_API_KEY, then hidden prompt.")
    parser.add_argument("--model", default="gpt-5.5", help="Outer Responses model.")
    parser.add_argument("--image-model", default="gpt-image-2", help="image_generation tool model.")
    parser.add_argument("--tool-action", choices=["generate", "edit", "auto"], help="Optional image_generation action.")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--out", required=True, help="Output image path.")
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--save-request-json", help="Write request JSON without secrets.")
    args = parser.parse_args()

    codex_config = _read_codex_config()
    codex_auth = _read_codex_auth()
    base_url = _get_base_url(args, codex_config)
    prompt = _read_prompt(args)
    api_key = _get_api_key(args, codex_auth)
    payload = _build_payload(args, prompt)
    _save_request_json(args.save_request_json, payload)
    print(json.dumps({"base_url": base_url, "model": args.model, "image_model": args.image_model}, ensure_ascii=False), flush=True)
    return _post_stream(args, base_url, api_key, payload)


if __name__ == "__main__":
    raise SystemExit(main())
