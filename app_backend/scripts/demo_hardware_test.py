"""Interactive, operator-confirmed test for a physical ECURIOUS parking lock.

The script talks to the local FastAPI application, not directly to the
provider.  With the default ``status`` action it performs read-only checks.
Every physical write requires both ``--allow-write`` and an exact confirmation
phrase typed immediately before the command is sent.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from typing import Any
from urllib.parse import quote

import httpx


class DemoError(RuntimeError):
    """An expected, operator-actionable demo failure."""


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely test a physical ECURIOUS lock through the local FastAPI backend."
    )
    parser.add_argument(
        "--action",
        choices=("status", "unlock", "lock", "cycle"),
        default="status",
        help="status is read-only; cycle lowers and then raises the barrier",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="arm physical writes; an exact interactive confirmation is still required",
    )
    parser.add_argument(
        "--lock-id",
        default=os.getenv("ECURIOUS_LOCK_ID", "").strip(),
        help="exact assigned lock ID (defaults to ECURIOUS_LOCK_ID)",
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("APP_BACKEND_URL", "http://127.0.0.1:8001").strip(),
        help="local app backend URL",
    )
    parser.add_argument(
        "--admin-email",
        default=os.getenv("DEMO_ADMIN_EMAIL", "").strip(),
        help="local app ADMIN email; prompted when omitted",
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=45.0,
        help="maximum time to wait for a physical state report",
    )
    return parser.parse_args()


def _response_body(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
) -> Any:
    try:
        response = await client.request(method, path, headers=headers, json=json)
    except httpx.TimeoutException as exc:
        raise DemoError(f"Local backend timed out while calling {path}") from exc
    except httpx.RequestError as exc:
        raise DemoError(
            "Cannot reach the local backend. Start uvicorn on the configured --base-url first."
        ) from exc

    body = _response_body(response)
    if response.is_error:
        if isinstance(body, dict):
            code = body.get("error", f"HTTP_{response.status_code}")
            message = body.get("message", "Request failed")
        else:
            code = f"HTTP_{response.status_code}"
            message = "The local backend returned a non-JSON error"
        raise DemoError(f"{code}: {message}")
    return body


async def login(
    client: httpx.AsyncClient, admin_email: str
) -> dict[str, str]:
    email = admin_email or input("Local ADMIN email: ").strip()
    password = os.getenv("DEMO_ADMIN_PASSWORD", "") or getpass.getpass(
        "Local ADMIN password (hidden): "
    )
    if not email or not password:
        raise DemoError("An ADMIN email and password are required")
    body = await request_json(
        client,
        "POST",
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
        raise DemoError("The local backend returned an invalid login response")
    user = body.get("user")
    if not isinstance(user, dict) or user.get("role") != "ADMIN":
        raise DemoError("This demo requires a local ADMIN account")
    return {"Authorization": f"Bearer {body['access_token']}"}


async def get_bay(
    client: httpx.AsyncClient, headers: dict[str, str], lock_id: str
) -> dict[str, Any]:
    body = await request_json(client, "GET", "/api/admin/bays", headers=headers)
    if not isinstance(body, list):
        raise DemoError("The local backend returned an invalid bay list")
    bay = next(
        (
            item
            for item in body
            if isinstance(item, dict) and item.get("bay_id") == lock_id
        ),
        None,
    )
    if bay is None:
        raise DemoError(f"Lock {lock_id!r} is not assigned to this API key")
    return bay


def print_status(bay: dict[str, Any], *, label: str) -> None:
    print(f"\n[{label}]")
    print(f"  lock:       {bay.get('bay_id')}")
    print(f"  online:     {bay.get('hardware_online')}")
    print(f"  barrier:    {bay.get('barrier_status')}")
    print(f"  occupancy:  {bay.get('occupancy')}")
    print(f"  last seen:  {bay.get('last_seen_at')}")
    print(f"  4G signal:  {bay.get('signal_strength_4g')}")
    print(f"  water:      {bay.get('water_ingress')}")
    print(f"  errors:     {bay.get('active_errors') or []}")


def require_safe_status(bay: dict[str, Any], action: str) -> None:
    if bay.get("hardware_online") is not True:
        raise DemoError("The lock is offline; no physical command will be sent")
    if bay.get("occupancy") != "free":
        raise DemoError("The bay is not confirmed free; no physical command will be sent")
    if bay.get("water_ingress") is not False:
        raise DemoError("Water-ingress status is not confirmed safe; no command will be sent")
    errors = bay.get("active_errors")
    if not isinstance(errors, list):
        raise DemoError("Hardware-error status is unknown; no physical command will be sent")
    if errors:
        raise DemoError("Active hardware errors are present; no physical command will be sent")

    barrier = bay.get("barrier_status")
    expected = "raised" if action == "unlock" else "lowered"
    if barrier != expected:
        raise DemoError(
            f"{action} requires barrier={expected}, but the reported state is {barrier!r}"
        )


def confirm_physical_write(action: str, lock_id: str) -> bool:
    phrase = f"{action.upper()} {lock_id}"
    print("\nPHYSICAL MOVEMENT WARNING")
    print("Confirm that nobody, no vehicle, and no loose object is in the barrier path.")
    print(f"Type exactly: {phrase}")
    return input("> ").strip() == phrase


async def send_command(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    lock_id: str,
    action: str,
) -> None:
    if not confirm_physical_write(action, lock_id):
        raise DemoError("Confirmation did not match; no physical command was sent")
    encoded_lock_id = quote(lock_id, safe="")
    body = await request_json(
        client,
        "POST",
        f"/api/bays/{encoded_lock_id}/{action}",
        headers=headers,
    )
    if not isinstance(body, dict):
        raise DemoError("The backend returned an invalid command response")
    print(
        f"Command {body.get('command_id')} status={body.get('command_status')} "
        f"physical_state={body.get('physical_state')}"
    )
    if body.get("command_status") != "ACCEPTED":
        raise DemoError("The hardware command was not accepted")


async def wait_for_barrier(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    lock_id: str,
    expected: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_seconds
    while True:
        bay = await get_bay(client, headers, lock_id)
        if bay.get("barrier_status") == expected:
            print_status(bay, label=f"CONFIRMED {expected.upper()}")
            return bay
        if bay.get("hardware_online") is not True:
            raise DemoError("The lock went offline while waiting for physical confirmation")
        if loop.time() >= deadline:
            raise DemoError(
                f"Timed out waiting for barrier={expected}. The accepted command is not "
                "proof of physical movement; inspect the device before doing anything else."
            )
        await asyncio.sleep(2.0)


async def run(args: argparse.Namespace) -> None:
    if not args.lock_id:
        raise DemoError("Pass --lock-id or set ECURIOUS_LOCK_ID")
    if args.poll_seconds <= 0:
        raise DemoError("--poll-seconds must be greater than zero")
    if args.action != "status" and not args.allow_write:
        raise DemoError(
            "Write action refused. Add --allow-write only when the physical test area is ready."
        )
    if args.action != "status" and not sys.stdin.isatty():
        raise DemoError("Physical writes require an interactive terminal")

    timeout = httpx.Timeout(15.0)
    async with httpx.AsyncClient(base_url=args.base_url.rstrip("/"), timeout=timeout) as client:
        health = await request_json(client, "GET", "/api/health")
        if not isinstance(health, dict) or health.get("mode") != "live":
            raise DemoError("The local backend is not running in ECURIOUS live mode")

        headers = await login(client, args.admin_email)
        bay = await get_bay(client, headers, args.lock_id)
        print_status(bay, label="READ-ONLY PREFLIGHT")

        if args.action == "status":
            print("\nRead-only check complete. No hardware command was sent.")
            return

        first_action = "unlock" if args.action in {"unlock", "cycle"} else "lock"
        require_safe_status(bay, first_action)
        await send_command(client, headers, args.lock_id, first_action)
        expected = "lowered" if first_action == "unlock" else "raised"
        await wait_for_barrier(
            client, headers, args.lock_id, expected, args.poll_seconds
        )

        if args.action != "cycle":
            print("\nHardware test complete.")
            return

        print("\nUnlock was physically confirmed. Re-check the real test area before raising it.")
        bay = await get_bay(client, headers, args.lock_id)
        require_safe_status(bay, "lock")
        await send_command(client, headers, args.lock_id, "lock")
        await wait_for_barrier(
            client, headers, args.lock_id, "raised", args.poll_seconds
        )
        print("\nHardware cycle test complete.")


def main() -> None:
    try:
        asyncio.run(run(parse_args()))
    except (DemoError, KeyboardInterrupt) as exc:
        message = str(exc) or "Cancelled by operator"
        print(f"\nSTOPPED: {message}", file=sys.stderr)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
