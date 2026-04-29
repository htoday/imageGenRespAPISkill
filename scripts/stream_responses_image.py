#!/usr/bin/env python3
"""Generate an image through streamed Responses image_generation calls.

This script is intentionally dependency-free. It uses urllib so it can run
outside Codex without installing the OpenAI SDK.
"""

from __future__ import annotations

import argparse
import base64
import json
from pathlib import Path
import socket
import sys
from urllib.parse import urlsplit, urlunsplit
import urllib.error
import urllib.request


def _normalize_base_url(value: str) -> str:
    value = value.strip()
    if not value:
        raise SystemExit("base_url is required")
    return value.rstrip("/")


def _mask_secret(value: str) -> str:
    if len(value) <= 8:
        return "<redacted>"
    return f"{value[:4]}...{value[-4:]}"


def _mask_base_url(value: str) -> str:
    try:
        parts = urlsplit(value)
    except ValueError:
        return "<invalid-url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


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
    parser.add_argument("--base-url", required=True, help="OpenAI-compatible base URL. Must be passed explicitly.")
    parser.add_argument("--api-key", required=True, help="API key. Must be passed explicitly; it is never written to files.")
    parser.add_argument("--model", default="gpt-5.5", help="Outer Responses model.")
    parser.add_argument("--image-model", default="gpt-image-2", help="image_generation tool model.")
    parser.add_argument("--tool-action", choices=["generate", "edit", "auto"], help="Optional image_generation action.")
    parser.add_argument("--prompt")
    parser.add_argument("--prompt-file")
    parser.add_argument("--out", required=True, help="Output image path.")
    parser.add_argument("--timeout", type=float, default=420.0)
    parser.add_argument("--save-request-json", help="Write request JSON without secrets.")
    args = parser.parse_args()

    base_url = _normalize_base_url(args.base_url)
    prompt = _read_prompt(args)
    payload = _build_payload(args, prompt)
    _save_request_json(args.save_request_json, payload)
    print(
        json.dumps(
            {
                "base_url": _mask_base_url(base_url),
                "api_key": _mask_secret(args.api_key),
                "model": args.model,
                "image_model": args.image_model,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return _post_stream(args, base_url, args.api_key, payload)


if __name__ == "__main__":
    raise SystemExit(main())
