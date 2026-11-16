# Ecurious EV Charging Reservation System

A full-stack prototype for EV drivers and Charge Point Operators (CPOs), featuring map-based charging station discovery, user accounts and vehicle management, reservation conflict handling, ECURIOUS physical parking locks, automatic check-in, No-Show handling, Blocked Bay management, operational alerts, and analytics.

## Hackathon Challenge Context

This project was developed for the **Ecurious Challenge: Smart Reservation for Public EV Charging** at the Watt the Hack hackathon. The challenge asks teams to add a reliable reservation feature to a public EV charging app: a driver should be able to find and reserve a charge point and, crucially, find the booked bay physically available on arrival. The problem deliberately spans software, UX, energy systems, hardware, and business rather than assuming prior e-mobility expertise.

### Challenge Setup

The hackathon provides an end-to-end test environment:

- **Ecurious reservation hardware** installed at a test charging bay physically protects the space for the driver who booked it.
- A **built-in camera with license plate recognition (LPR)** identifies the vehicle occupying or arriving at the bay.
- The **Ecurious server** exposes the hardware controls and status, including reserve/release operations and license-plate events.
- **GoingElectric** supplies public charge-point information such as locations, connectors, and charging power for map and search features.
- Ecurious mentors, on-site credentials, API documentation, and the installed test bay are available during the hackathon. OCPP `ReserveNow` / `CancelReservation` and OCPI reservation commands are suggested as interoperability references.

The mandatory core is a working integration across **charging app <-> Ecurious server <-> reservation hardware**, demonstrated through the complete journey:

`find charger -> reserve -> arrive and check in via LPR -> charge -> release`

### Expected Deliverables

Teams are expected to:

1. Build a mobile or web prototype that displays public charge points using GoingElectric data.
2. Demonstrate the full reservation cycle on the supplied Ecurious hardware.
3. Document the architecture, API calls, data flows, design decisions, tools, and unsuccessful approaches.
4. Address at least two deeper mission areas.
5. Explain limitations and how the solution could scale to other apps, operators, locations, and hardware.

The optional mission areas are **No-Shows**, **Blocked Bays**, **License Plate Recognition**, **Interoperability**, **New Use Cases** (including events, retail, hotels, workplaces, and fleets), **Utilisation**, and the **Business Case** for reservation pricing, no-show fees, subscriptions, or revenue sharing. A polished interface is a bonus; the emphasis is on a thoughtful, documented, and transferable solution.

### Evaluation and Prize

The jury scores each category from 1 to 5 and applies the following weights:

| Criterion | Weight | Focus |
| --- | ---: | --- |
| Technical Implementation | 30% | Live app/server/hardware integration, complete reservation cycle, edge cases, and code quality |
| Solution Concept & Innovation | 20% | Original mission-area solutions, creative LPR use, and new use cases |
| Interoperability & Scalability | 15% | Open APIs, OCPI/OCPP alignment, and transferability |
| Business Case & CPO Value | 20% | Plausible pricing, stakeholder value, and measurable utilisation impact |
| Documentation & Pitch | 15% | Architecture and decision records, API documentation, lessons learned, demo, and presentation |

The live hardware reservation cycle is required for a top ranking, and Technical Implementation is the tie-breaker. The winning team receives **EUR 1,000**.

### Fair Play, Safety, and Privacy

Only the challenge-provided hardware and server access may be used; public or production charging infrastructure must not be disturbed. Implementations must respect GoingElectric and other data-source terms. User, location, and license-plate data must be handled responsibly and in accordance with GDPR, including data minimisation, appropriate retention, and consent. Clear privacy violations may reduce the jury score.

The project maintains two explicitly separated operating modes:

- `mock`: Uses the fictional Mock Server included in the repository, suitable for development, automated testing, and full demonstrations.
- `live`: Uses the official ECURIOUS REST API and Server-Sent Events (SSE) provided during the hackathon to connect to the physical parking lock assigned to the team.

Live mode never automatically falls back to Mock mode when an operation fails. Charging status and revenue figures remain explicitly labeled as Simulation / Estimated.

## Architecture

```
React + Vite :5173
        |
        | /api (API keys never enter the browser)
        v
FastAPI App Backend :8001 ---- SQLite ecurious_app.db
        |
        +---- EcuriousAdapter (ECURIOUS_API_MODE)
                 | mock                    | live
                 v                         v
       Mock Server :8000        Official REST API + SSE
                                             |
                                             v
                                  Physical Parking Lock
```

- `frontend/app/`: Leaflet-based map, driver interface, and CPO Admin Dashboard. No frontend modifications were required for this hardware integration.
- `app_backend/`: Authentication, vehicle management, local reservation transactions, access control, hardware whitelisting, SSE, alerts, and analytics.
- `backend/`: Preserved fictional Mock Server. It is never silently invoked when Live mode fails.
- `openapi.yaml`: Legacy Mock API contract, which does not represent the official hardware protocol.

The physical hardware API controls only the parking lock and does not provide reservation management resources. Therefore, in Live mode, the App Backend continues to store and coordinate reservations using SQLite and securely synchronizes the currently authorized license plates to the hardware EEPROM.

## One-Time Setup

Requires Python 3.11+ and Node.js 20+.

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r backend/requirements.txt -r app_backend/requirements.txt
cd frontend/app
npm ci
```

Services read environment variables from the process environment. They do not automatically load `.env` files.

## Starting in Mock Mode (macOS / Linux)

Terminal 1: Mock Server (8000)

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/backend
source ../.venv/bin/activate
set -a
source .env.example
set +a
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

Terminal 2: App Backend (8001)

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/app_backend
source ../.venv/bin/activate
set -a
source .env.mock.example
set +a
python -m uvicorn app_backend.main:app --host 127.0.0.1 --port 8001 --reload
```

Terminal 3: React (5173)

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/frontend/app
npm run dev
```

## Starting in Live Mode (macOS / Linux)

Do not start the `backend/` Mock Server in Live mode.

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/app_backend
cp .env.example .env
```

Enter the actual API key only in the local `.env` file:

```
ECURIOUS_API_BASE_URL=https://parking-lock-service-production.up.railway.app
ECURIOUS_API_KEY=REPLACE_WITH_YOUR_KEY
ECURIOUS_API_MODE=live
ECURIOUS_LOCK_ID=lock-0001
ECURIOUS_TIMEOUT_SECONDS=10
ECURIOUS_PLATE_POLL_SECONDS=5
SIMULATED_CHARGING_RATE_EUR_PER_HOUR=0.60
```

Then start the service:

```
source ../.venv/bin/activate
set -a
source .env
set +a
python -m uvicorn app_backend.main:app --host 127.0.0.1 --port 8001 --reload
```

In another terminal, start the frontend as usual using `npm run dev` in `frontend/app`.

The `.env` file is excluded by `.gitignore`. Never place real API keys in `VITE_*` variables, React code, YAML files, or command-line URLs.

The Live configuration template sets `APP_SEED_DEMO_USERS=false` by default, preventing the creation of accounts with predefined passwords. ADMIN accounts must be provisioned through a trusted local deployment procedure. The public registration endpoint can only create DRIVER accounts.

When using a separate Live database for the first time, an administrator can be created interactively in a terminal where `.env` has already been loaded:

```
python scripts/create_admin.py
```

The script only writes to the local SQLite database and does not connect to or control any hardware.

The Live configuration uses `ecurious_live.db` by default, preventing Mock accounts with predefined demo credentials from being introduced into the physical hardware environment.

## Windows PowerShell

Install dependencies and start the App Backend:

```
cd C:\path\to\PythonProject_hack
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r backend\requirements.txt -r app_backend\requirements.txt
cd app_backend
Copy-Item .env.example .env
# Edit .env and enter ECURIOUS_API_KEY
Get-Content .env | ForEach-Object {
  if ($_ -match '^\s*([^#][^=]*)=(.*)$') {
    Set-Item -Path "Env:$($matches[1].Trim())" -Value $matches[2].Trim()
  }
}
python -m uvicorn app_backend.main:app --host 127.0.0.1 --port 8001 --reload
```

Frontend:

```
cd C:\path\to\PythonProject_hack\frontend\app
npm ci
npm run dev
```

For Mock mode, replace `Copy-Item .env.example .env` with `Copy-Item .env.mock.example .env` and start `backend` in a separate terminal.

## Read-Only Live Verification (No Hardware State Changes)

Load the Live `.env` configuration before running:

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/app_backend
source ../.venv/bin/activate
set -a
source .env
set +a
python scripts/verify_live_readonly.py
```

The script only calls:

- `GET /api/v1/locks`
- `GET /api/v1/locks/{lockId}`

It does not invoke `unlock`, `lock`, `keep-state`, or `whitelist`.

No write operations were sent to the physical hardware during this development and automated testing process.

## Physical Hardware Demo Requiring Manual Confirmation

First, start the local `app_backend` in Live mode. Then, in another terminal, load `.env` and perform the read-only preflight check:

```
cd /Users/ingchaolee/PycharmProjects/PythonProject_hack/app_backend
source ../.venv/bin/activate
set -a
source .env
set +a
python scripts/demo_hardware_test.py --lock-id lock-0001
```

Only after confirming that the test area is secured and that no people, vehicles, or obstacles are within the parking barrier's movement path should an explicit lower → verify state → raise → verify state cycle be initiated:

```
python scripts/demo_hardware_test.py \
  --action cycle \
  --allow-write \
  --lock-id lock-0001
```

The script authenticates with the local FastAPI service as ADMIN and invokes `/api/bays/{id}/unlock` and `/api/bays/{id}/lock`. This preserves the existing authorization checks and command audit logging.

The script enforces the following safeguards:

- Without `--action`, the script is read-only. Any write operation additionally requires the explicit `--allow-write` flag.
- Before each physical command is sent, the operator must manually enter `UNLOCK lock-0001` or `LOCK lock-0001`. Any mismatch immediately stops execution.
- Write operations are rejected in non-interactive terminals. The script cannot be used for CI pipelines or unattended background execution.
- Operations are rejected if the device is Offline, the parking bay is not `free`, water intrusion is detected, active errors exist, or the initial barrier state does not match expectations.
- API command acceptance does not mean the physical operation has completed. The script must poll the device until `lowered` or `raised` is confirmed. On timeout, execution stops without automatically issuing a reverse command.
- The demo does not modify the whitelist or persistent `keep-state` settings.

If the cycle stops after lowering the barrier, first verify on-site that the parking bay is completely empty. Then execute `--action lock --allow-write` separately.

Do not assume the barrier has physically moved simply because the API returned `ACCEPTED`.

## Live Adapter Behavior

Official API endpoints follow the [public Hackathon documentation](https://parking-lock-service-production.up.railway.app/?utm_source=chatgpt.com):

- `GET /api/v1/locks`
- `GET /api/v1/locks/{lockId}`
- `POST /api/v1/locks/{lockId}/unlock`
- `POST /api/v1/locks/{lockId}/lock`
- `POST /api/v1/locks/{lockId}/keep-state`
- `POST /api/v1/locks/{lockId}/whitelist`
- `GET /api/v1/locks/{lockId}/plate/latest`
- `GET /events` (SSE; the official API also provides `/api/v1/events`)

REST requests use `X-API-Key` authentication. SSE uses the Team Key according to the official EventSource contract. HTTP client request logging is explicitly restricted to the Warning level to prevent SSE URLs containing API keys from appearing in logs.

The adapter converts official API fields into the fields expected by the existing frontend, including `occupancy`, `protection`, and `last_seen_at`.

HTTP 401, 403, 404, 429, and 5xx responses, timeouts, network failures, and Offline devices all produce explicit errors rather than false success responses.

Hardware write operations are not automatically retried by default. Failed whitelist synchronization is retried only when the authorized set changes, when an identical idempotent request is explicitly retried, or when an ADMIN invokes `/api/admin/sync`.

Service startup, module imports, health checks, and SSE reconnections perform only local initialization or read-only state recovery. They do not issue hardware write commands.

Background maintenance first establishes a read-only baseline during startup and subsequently synchronizes the whitelist only when reservation authorization time boundaries actually change.

Consequently, after restarting the Live service, operators should first inspect the read-only device state and then explicitly invoke `/api/admin/sync` to reconfirm the current EEPROM authorization set.

A service restart alone is never treated as authorization to modify a physical device.

## Reservation and Hardware Safety Rules

- Reservation conflicts are coordinated through SQLite `BEGIN IMMEDIATE` transactions.
- The EEPROM whitelist contains only license plates authorized within the current reservation time window, including the configured early-arrival grace period. Wildcard authorization is never enabled by default.
- A maximum of 10 unique license plates is supported. Exceeding this limit returns `WHITELIST_CAPACITY_EXCEEDED`.
- Reservation creation, cancellation, release, and No-Show handling all update the persisted hardware synchronization state.
- In Live mode, a Release operation first persists `RELEASE_PENDING` and the corresponding operation log. It then reads the physical occupancy state and synchronizes all currently valid authorizations for that Lock. The reservation transitions to `COMPLETED` only after the hardware confirms that authorization has been revoked and the parking bay is `free`. Failures retain `RELEASE_FAILED`, while timeouts or ambiguous results retain `RELEASE_UNKNOWN`. An ADMIN can retry the operation idempotently after refreshing the device state.
- If an active vehicle remains in the parking bay, Live Release returns `RELEASE_NOT_SAFE`. No barrier-raising command or whitelist revocation is sent. Concurrent or duplicate Release requests cannot repeatedly complete the same state transition.
- The No-Show deadline is consistently defined as `start_time + ECURIOUS_RESERVATION_GRACE_MINUTES`. Before expiration, the system reads the physical device state. Offline, `unknown`, or `occupied` conditions are flagged for manual review. Checked-in, canceled, and completed reservations cannot expire again or incur duplicate charges.
- Write operations targeting the same Lock are protected by both an in-process lock and a SQLite lease to prevent concurrent state overwrites.
- A correctly recognized license plate triggers immediate check-in. `carPresence` remains an independent source of physical occupancy telemetry.
- When an SSE `photo_captured` event contains an empty license plate, the system immediately performs a read-only lookup using `plate/latest`. If the license plate is still missing or the photo timestamp does not match, the event is logged and ignored without generating `WRONG_PLATE`.
- In Live mode, the system performs read-only queries for the latest license plate every `ECURIOUS_PLATE_POLL_SECONDS` (5 seconds by default). This provides a fallback for dropped SSE events or differences in OCR reporting timing. Photo timestamps and event IDs are used for persistent deduplication.
- ADMIN `lock` and `keep_closed` operations are rejected while a vehicle is still occupying the parking bay.
- Successful API command execution indicates only that the command has been accepted. Audit records are marked as physically confirmed only after the corresponding barrier state is received through SSE.
- After selecting Stop Charging, drivers can click `I've left / 已离开`. The backend completes the reservation, revokes the vehicle's authorization, and sends a barrier-raising command only when the parking bay sensor explicitly reports `free`. Repeated clicks reuse the original command. The barrier is not raised if the bay remains occupied, the state is unknown, or the device is Offline.
- If OCR fails to recognize a European license plate, a user with a currently valid reservation can invoke `POST /api/bays/{lockId}/unlock`. ADMIN users can also perform this action. All successful, failed, and rejected operations are recorded in `hardware_commands`.

In Live mode, the background SSE listener supports `status_change`, `vehicle_presence`, `photo_captured`, and `alarm` events.

After disconnection, it reconnects using exponential backoff and refreshes the Lock state. It does not assume that the official API supports event cursors or replay.

Each Lock maintains independent persistent high-water marks for each event type. Old events, events with identical timestamps, and duplicate event IDs do not trigger additional business side effects.

Events with missing or invalid timestamps are recorded as `INVALID_TIMESTAMP_IGNORED`. Their receipt times are not used to overwrite known hardware states.

A valid license plate event directly triggers check-in.

A reservation enters `ATTENTION_REQUIRED` only when a non-empty license plate is obtained and does not match the currently valid reservation. An empty license plate alone is never treated as an incorrect license plate.

Manual Unlock remains an authorized fallback in Live mode when the camera cannot recognize a license plate.

Simulated charging is stored in a separate `charging_sessions` table with the following fields:

- Session ID
- Server-side start and end timestamps
- Session duration
- Configured charging rate
- Estimated Cost
- Session status
- `is_simulated=true`

Repeated Start/Stop requests are idempotent. Releasing a reservation with an active charging session first stops that session.

The frontend displays a charging timer and estimated cost in the driver's reservation details.

These simulated values do not represent actual electricity consumption, charging power, payments, or invoices.

## Key App Backend APIs

```
POST /api/auth/register                 POST /api/auth/login
GET/POST/PATCH/DELETE /api/vehicles
GET  /api/stations                      GET /api/bays/{id}/availability
POST /api/reservations                  GET /api/reservations/my
POST /api/reservations/{id}/cancel
POST /api/charging/sessions             # DRIVER's own checked-in reservation
GET  /api/charging/sessions/{sessionId}
POST /api/charging/sessions/{sessionId}/stop
POST /api/bays/{id}/unlock              # User with valid reservation or ADMIN
POST /api/bays/{id}/lock                # ADMIN; parking bay must be unoccupied
POST /api/bays/{id}/keep-state          # ADMIN
GET  /api/admin/bays                    GET /api/admin/events
GET  /api/admin/alerts                  GET /api/admin/analytics
POST /api/admin/sync                    # Explicit hardware sync retry in Live mode
POST /api/admin/demo/*                  # Mock mode only
```

Access URLs:

- Web App:
- App Backend Swagger:
- Mock Server Swagger:

## Mock Demo

Local demonstration accounts defined in `.env.mock.example`:

| Role   | Email               | Development Password |
| ------ | ------------------- | -------------------- |
| DRIVER | `driver@demo.local` | `DriverDemo123!`     |
| ADMIN  | `admin@demo.local`  | `AdminDemo123!`      |

Standard Demo Workflow

A DRIVER creates a reservation for `GE-26 / BAY-001`.

Using the Demo Control Room, an ADMIN sequentially simulates a correct license plate, vehicle arrival, charging, and departure.

In Mock mode, a correct license plate alone does not trigger check-in. An `Occupied` event for the same parking bay is also required. Both possible event sequences are supported.

An incorrect license plate combined with an occupied parking bay generates `WRONG_PLATE` / `BLOCKED_BAY`.

Running the No-Show simulation produces an `EXPIRED` reservation.

In Live mode, Demo controls return `DEMO_UNAVAILABLE`. They are never translated into physical hardware operations.

## Automated Verification

```
cd backend
../.venv/bin/python -m pytest

cd ../app_backend
../.venv/bin/python -m pytest

cd ../frontend/app
npm run lint
npm test
npm run build
```

Current test results:

```
Mock Server: 19 passed
App Backend: 55 passed
Frontend:    6 passed
Total:       80 passed, 0 failed
Oxlint:      0 warnings
Vite build:  successful
```

All Live mode tests use `httpx.MockTransport` or in-process Fake Adapters. They do not connect to or control physical parking locks.

Test coverage includes:

- REST API response parsing
- Mock/Live mode switching
- Environment-based API key configuration
- HTTP error handling
- Timeouts and Offline device handling
- SSE reconnection and event deduplication
- License plate matching against reservation time windows
- EEPROM capacity restrictions
- Whitelist updates for cancellations and No-Show handling
- Manual Unlock permissions
- Separation of API command acceptance from physically confirmed states

Legacy Mock regression tests, frontend routing tests, and additive database migration tests are preserved.

## Remaining Physical Hardware Validation

The following aspects still require verification using the actual physical hardware:

- The Lock ID assigned to the team's API key, on-site network conditions, and the update frequency of `lastSeenAt`.
- Actual latency and failure modes between command acceptance and the corresponding physical `status_change` event.
- Time required for EEPROM whitelist updates to take effect in the camera system, persistence after power loss, and behavior at the 10-entry capacity limit.
- Actual OCR performance on European license plates. The system provides authorized Manual Unlock as a fallback and does not fabricate successful LPR recognition.
- SSE keep-alive behavior and disconnection frequency when long-lived connections pass through on-site proxies or 4G networks.
- Actual alarm values for battery status, voltage, water intrusion, motor failures, and other hardware faults.

If the official protocol changes during on-site testing, only the field mappings in `app_backend/app_backend/adapters/live.py` need to be adjusted.

The React API and local reservation data model do not need to be rewritten.
