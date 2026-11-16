from __future__ import annotations

from email.message import EmailMessage

import app_backend.services.billing as billing_module


def complete_departure(client, reservation, driver_headers, admin_headers):
    checked_in = client.post(
        "/api/admin/demo/plate",
        headers=admin_headers,
        json={"bay_id": reservation["bay_id"], "license_plate": "M-AB1234", "confidence": 0.99},
    )
    assert checked_in.status_code == 200
    session = client.post(
        "/api/charging/sessions",
        headers=driver_headers,
        json={"reservation_id": reservation["id"]},
    )
    assert session.status_code == 201, session.text
    stopped = client.post(
        f"/api/charging/sessions/{session.json()['session_id']}/stop",
        headers=driver_headers,
    )
    assert stopped.status_code == 200, stopped.text
    departed = client.post(
        f"/api/reservations/{reservation['id']}/depart",
        headers=driver_headers,
    )
    assert departed.status_code == 200, departed.text
    assert departed.json()["reservation"]["status"] == "COMPLETED"


def test_completed_departure_emails_demo_pdf_once(
    client, app, reservation, driver_headers, admin_headers, monkeypatch
):
    complete_departure(client, reservation, driver_headers, admin_headers)
    sent: list[EmailMessage] = []
    monkeypatch.setattr(billing_module, "_smtp_send", lambda message, _settings: sent.append(message))

    invalid = client.post(
        f"/api/reservations/{reservation['id']}/bill",
        headers=driver_headers,
        json={"email": "not-an-email"},
    )
    assert invalid.status_code == 422

    first = client.post(
        f"/api/reservations/{reservation['id']}/bill",
        headers=driver_headers,
        json={"email": "customer@example.com"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["status"] == "SENT"
    assert first.json()["already_sent"] is False
    assert len(sent) == 1
    assert "Demo Bill" in sent[0]["Subject"]
    assert "customer@example.com" in sent[0]["To"]
    attachment = next(sent[0].iter_attachments())
    assert attachment.get_content_type() == "application/pdf"
    assert attachment.get_payload(decode=True).startswith(b"%PDF-")

    duplicate = client.post(
        f"/api/reservations/{reservation['id']}/bill",
        headers=driver_headers,
        json={"email": "different@example.com"},
    )
    assert duplicate.status_code == 200, duplicate.text
    assert duplicate.json()["already_sent"] is True
    assert duplicate.json()["recipient_email"] == "customer@example.com"
    assert len(sent) == 1

    with app.state.database.connect() as connection:
        delivery = connection.execute(
            "SELECT * FROM bill_deliveries WHERE reservation_id = ?",
            (reservation["id"],),
        ).fetchone()
    assert delivery["status"] == "SENT"
    assert delivery["pdf_sha256"]
    assert delivery["last_error"] is None


def test_bill_failure_is_generic_and_can_be_retried(
    client, app, reservation, driver_headers, admin_headers, monkeypatch
):
    complete_departure(client, reservation, driver_headers, admin_headers)

    def fail_send(_message, _settings):
        raise OSError("test SMTP failure")

    monkeypatch.setattr(billing_module, "_smtp_send", fail_send)
    failed = client.post(
        f"/api/reservations/{reservation['id']}/bill",
        headers=driver_headers,
        json={"email": "customer@example.com"},
    )
    assert failed.status_code == 503
    assert failed.json()["error"] == "BILL_EMAIL_FAILED"
    assert "test SMTP failure" not in failed.text

    with app.state.database.connect() as connection:
        delivery = connection.execute(
            "SELECT * FROM bill_deliveries WHERE reservation_id = ?",
            (reservation["id"],),
        ).fetchone()
    assert delivery["status"] == "FAILED"
    assert delivery["last_error"] == "SMTP_DELIVERY_FAILED"

    sent: list[EmailMessage] = []
    monkeypatch.setattr(billing_module, "_smtp_send", lambda message, _settings: sent.append(message))
    retried = client.post(
        f"/api/reservations/{reservation['id']}/bill",
        headers=driver_headers,
        json={"email": "customer@example.com"},
    )
    assert retried.status_code == 200, retried.text
    assert retried.json()["status"] == "SENT"
    assert len(sent) == 1
