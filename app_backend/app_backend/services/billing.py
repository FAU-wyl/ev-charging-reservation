from __future__ import annotations

import asyncio
import hashlib
import json
import re
import smtplib
import ssl
import uuid
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import formataddr
from html import escape
from io import BytesIO
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app_backend.config import Settings
from app_backend.database import Database, now_iso
from app_backend.errors import AppError, conflict, forbidden, not_found
from app_backend.security import mask_plate


TEMPLATE_PATH = Path(__file__).resolve().parents[1] / "templates" / "invoice_template.html"
GREEN = colors.HexColor("#16A34A")
DARK_GREEN = colors.HexColor("#052E16")
DARK = colors.HexColor("#0F172A")
MUTED = colors.HexColor("#64748B")
LINE = colors.HexColor("#E2E8F0")
PALE_GREEN = colors.HexColor("#F0FDF4")
PALE_ORANGE = colors.HexColor("#FFF7ED")
ORANGE = colors.HexColor("#9A3412")


def _time(value: str | None) -> str:
    if not value:
        return "N/A"
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%d %b %Y %H:%M UTC")


def _money(value: float | int | None) -> str:
    return f"EUR {float(value or 0):.2f}"


def _pdf_safe(value: Any) -> str:
    """Keep portable built-in PDF fonts while further masking non-Latin identifiers."""
    return re.sub(r"[^\x20-\x7E\xA0-\xFF]", "*", str(value))


def _smtp_send(message: EmailMessage, settings: Settings) -> None:
    if not settings.smtp_username or not settings.smtp_password:
        raise RuntimeError("SMTP is not configured")
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(
        settings.smtp_host,
        settings.smtp_port,
        timeout=settings.smtp_timeout_seconds,
        context=context,
    ) as client:
        client.login(settings.smtp_username, settings.smtp_password)
        client.send_message(message)


class BillingService:
    def __init__(self, database: Database, settings: Settings) -> None:
        self.database = database
        self.settings = settings

    def _bill_data(self, reservation_id: str, user: Any) -> dict[str, Any]:
        with self.database.connect() as connection:
            row = connection.execute(
                """
                SELECT r.*, u.email AS user_email, v.license_plate,
                       s.name AS station_name, s.chargepoints_json,
                       cs.session_id, cs.started_at AS charging_started_at,
                       cs.ended_at AS charging_ended_at,
                       cs.duration_seconds, cs.rate_eur_per_hour,
                       cs.estimated_cost_eur
                FROM app_reservations r
                JOIN users u ON u.id = r.user_id
                JOIN vehicles v ON v.id = r.vehicle_id
                JOIN stations s ON s.id = r.station_id
                LEFT JOIN charging_sessions cs ON cs.session_id = (
                    SELECT latest.session_id FROM charging_sessions latest
                    WHERE latest.reservation_id = r.id
                    ORDER BY latest.started_at DESC LIMIT 1
                )
                WHERE r.id = ?
                """,
                (reservation_id,),
            ).fetchone()
            if row is None:
                raise not_found("Reservation was not found")
            if user["role"] != "ADMIN" and row["user_id"] != user["id"]:
                raise forbidden("Drivers can only request bills for their own reservations")
            if row["status"] != "COMPLETED":
                raise conflict(
                    "BILL_REQUIRES_COMPLETED_DEPARTURE",
                    "Confirm departure before requesting a bill",
                )
            if not row["session_id"] or row["charging_ended_at"] is None:
                raise conflict(
                    "BILL_REQUIRES_COMPLETED_CHARGING",
                    "A completed charging session is required for this bill",
                )
            fees = connection.execute(
                """SELECT charge_type, amount_eur, status, description
                   FROM reservation_charges WHERE reservation_id = ?
                   ORDER BY created_at""",
                (reservation_id,),
            ).fetchall()
            arrival = connection.execute(
                """SELECT MIN(timestamp) FROM processed_events
                   WHERE reservation_id = ? AND processing_result = 'CHECK_IN_SUCCESSFUL'""",
                (reservation_id,),
            ).fetchone()[0]

        chargepoints = json.loads(row["chargepoints_json"] or "[]")
        chargepoint = chargepoints[0] if chargepoints else {}
        duration_seconds = int(row["duration_seconds"] or 0)
        charging_fee = float(row["estimated_cost_eur"] or 0)
        charge_items = [
            {
                "label": "Simulated charging time",
                "basis": f"{max(1, round(duration_seconds / 60))} min at {_money(row['rate_eur_per_hour'])}/hour",
                "amount": charging_fee,
                "waived": False,
            }
        ]
        for fee in fees:
            charge_items.append(
                {
                    "label": str(fee["description"]),
                    "basis": str(fee["charge_type"]).replace("_", " ").title(),
                    "amount": 0.0 if fee["status"] == "WAIVED" else float(fee["amount_eur"]),
                    "waived": fee["status"] == "WAIVED",
                }
            )
        total = sum(item["amount"] for item in charge_items)
        return {
            "reservation_id": row["id"],
            "station_name": row["station_name"],
            "charger_id": chargepoint.get("type") or "Demo charger",
            "bay_id": row["bay_id"],
            "connector": chargepoint.get("type") or "N/A",
            "power_kw": chargepoint.get("power") or "N/A",
            "plate_masked": mask_plate(row["license_plate"]) or "***",
            "t_reserved": _time(row["created_at"]),
            "t_arrived": _time(arrival),
            "arrive_note": "Automatic plate check-in" if arrival else "Arrival recorded",
            "t_start": _time(row["charging_started_at"]),
            "t_end": _time(row["charging_ended_at"]),
            "t_release": _time(row["updated_at"]),
            "charge_minutes": max(1, round(duration_seconds / 60)),
            "hourly_rate": _money(row["rate_eur_per_hour"]),
            "charging_fee": _money(charging_fee),
            "charge_items": charge_items,
            "total_value": total,
            "total": _money(total),
        }

    @staticmethod
    def render_pdf(data: dict[str, Any]) -> bytes:
        buffer = BytesIO()
        styles = getSampleStyleSheet()
        normal = ParagraphStyle(
            "BillNormal", parent=styles["BodyText"], fontName="Helvetica",
            fontSize=9, leading=13, textColor=DARK,
        )
        small = ParagraphStyle(
            "BillSmall", parent=normal, fontSize=7.5, leading=10, textColor=MUTED,
        )
        section = ParagraphStyle(
            "BillSection", parent=styles["Heading2"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=DARK, spaceBefore=12, spaceAfter=7,
            leftIndent=7, borderColor=GREEN, borderWidth=0, borderPadding=0,
        )
        right = ParagraphStyle("BillRight", parent=small, alignment=TA_RIGHT, textColor=colors.white)
        center = ParagraphStyle("BillCenter", parent=normal, alignment=TA_CENTER)

        doc = SimpleDocTemplate(
            buffer, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm,
            topMargin=16 * mm, bottomMargin=16 * mm,
            title=f"Ecurious Demo Bill {data['invoice_no']}",
            author="Ecurious Demo",
        )
        story: list[Any] = []
        header = Table(
            [[Paragraph("<b>ECURIOUS</b><br/><font size='8'>Charging Bill</font>", ParagraphStyle(
                "Brand", parent=normal, fontSize=16, leading=18, textColor=colors.white,
            )), Paragraph(
                f"Bill No. <b>{escape(data['invoice_no'])}</b><br/>"
                f"Reservation <b>{escape(data['reservation_id'])}</b><br/>"
                f"Issued {escape(data['issued_at'])}<br/><b>DEMO - NOT PAID</b>", right,
            )]],
            colWidths=[85 * mm, 73 * mm],
        )
        header.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), DARK_GREEN),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 14),
            ("RIGHTPADDING", (0, 0), (-1, -1), 14),
            ("TOPPADDING", (0, 0), (-1, -1), 13),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 13),
        ]))
        story.extend([header, Spacer(1, 8)])
        demo = Table([[Paragraph("<b>DEMO BILL - SIMULATED / ESTIMATED COSTS - NOT A TAX INVOICE</b>", center)]], colWidths=[158 * mm])
        demo.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_ORANGE),
            ("TEXTCOLOR", (0, 0), (-1, -1), ORANGE),
            ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#FDBA74")),
            ("TOPPADDING", (0, 0), (-1, -1), 7),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
        ]))
        story.extend([demo, Spacer(1, 8)])

        def section_title(title: str) -> Table:
            block = Table([["", Paragraph(f"<b>{escape(title)}</b>", section)]], colWidths=[3, 155 * mm])
            block.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (0, 0), GREEN),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]))
            return block

        story.append(section_title("Charging Details"))
        details = [
            [Paragraph("<font color='#64748B'>Station</font><br/>" + escape(_pdf_safe(data["station_name"])), normal),
             Paragraph("<font color='#64748B'>Charger / Bay</font><br/>" + escape(_pdf_safe(f"{data['charger_id']} / {data['bay_id']}")), normal)],
            [Paragraph("<font color='#64748B'>Connector - Power</font><br/>" + escape(_pdf_safe(f"{data['connector']} - {data['power_kw']} kW")), normal),
             Paragraph("<font color='#64748B'>License Plate</font><br/>" + escape(_pdf_safe(data["plate_masked"])), normal)],
        ]
        detail_table = Table(details, colWidths=[79 * mm, 79 * mm])
        detail_table.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
        story.append(detail_table)

        story.append(section_title("Timeline"))
        timeline = [
            ["Stage", "Time", "Note"],
            ["Reservation created", data["t_reserved"], "-"],
            ["Vehicle arrived", data["t_arrived"], data["arrive_note"]],
            ["Charging started", data["t_start"], "-"],
            ["Charging ended", data["t_end"], f"{data['charge_minutes']} min"],
            ["Bay released", data["t_release"], "Departure confirmed"],
        ]
        timeline_table = Table(timeline, colWidths=[43 * mm, 69 * mm, 46 * mm])
        timeline_table.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("GRID", (0, 0), (-1, -1), 0.35, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("ALIGN", (2, 0), (2, -1), "RIGHT"),
        ]))
        story.append(timeline_table)

        story.append(section_title("Simulated Charging Summary"))
        stats = Table([[
            Paragraph(f"<b><font size='14' color='#16A34A'>{data['charge_minutes']}</font></b><br/><font color='#64748B'>Minutes</font>", center),
            Paragraph(f"<b><font size='12' color='#16A34A'>{escape(data['hourly_rate'])}</font></b><br/><font color='#64748B'>Hourly rate</font>", center),
            Paragraph(f"<b><font size='12' color='#16A34A'>{escape(data['charging_fee'])}</font></b><br/><font color='#64748B'>Charging estimate</font>", center),
        ]], colWidths=[52.6 * mm] * 3)
        stats.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_GREEN),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.white),
            ("INNERGRID", (0, 0), (-1, -1), 2, colors.white),
            ("TOPPADDING", (0, 0), (-1, -1), 9),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.append(stats)

        story.append(section_title("Charges"))
        charge_rows: list[list[Any]] = [["Item", "Basis", "Amount"]]
        for item in data["charge_items"]:
            amount = "Waived" if item["waived"] else _money(item["amount"])
            charge_rows.append([
                Paragraph(escape(item["label"]), normal),
                Paragraph(escape(item["basis"]), small),
                amount,
            ])
        charge_rows.extend([
            ["VAT", "Not calculated (Demo)", "EUR 0.00"],
            [Paragraph("<b>Estimated total</b>", normal), "", Paragraph(f"<b>{escape(data['total'])}</b>", normal)],
        ])
        charges = Table(charge_rows, colWidths=[70 * mm, 57 * mm, 31 * mm])
        charges.setStyle(TableStyle([
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("LINEBELOW", (0, 0), (-1, -2), 0.35, LINE),
            ("LINEABOVE", (0, -1), (-1, -1), 1.2, DARK),
            ("ALIGN", (-1, 0), (-1, -1), "RIGHT"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TEXTCOLOR", (-1, -1), (-1, -1), GREEN),
        ]))
        story.append(KeepTogether(charges))

        story.append(section_title("Payment"))
        payment = Table([[
            Paragraph("<font color='#64748B'>Method</font><br/>Demo simulation only", normal),
            Paragraph("<font color='#64748B'>Payment status</font><br/>No real payment processed", normal),
            Paragraph("<font color='#64748B'>Transaction ID</font><br/>N/A", normal),
        ]], colWidths=[52.6 * mm] * 3)
        story.extend([payment, Spacer(1, 10)])
        note = Table([[Paragraph(
            "This bill is generated for the ECURIOUS demonstration. Values are estimates based on the simulated charging duration and configured reservation fees.",
            normal,
        )]], colWidths=[158 * mm])
        note.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), PALE_GREEN),
            ("TOPPADDING", (0, 0), (-1, -1), 8),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
            ("LEFTPADDING", (0, 0), (-1, -1), 9),
            ("RIGHTPADDING", (0, 0), (-1, -1), 9),
        ]))
        story.extend([note, Spacer(1, 8), Paragraph(
            "Questions? Contact support@ecurious.example - This Demo Bill is not a tax invoice.",
            ParagraphStyle("Footer", parent=small, alignment=TA_CENTER),
        )])
        doc.build(story)
        return buffer.getvalue()

    @staticmethod
    def render_html(data: dict[str, Any]) -> str:
        rows = []
        for item in data["charge_items"]:
            amount = "Waived" if item["waived"] else _money(item["amount"])
            rows.append(
                "<tr><td>{}</td><td class='r'>{}</td><td class='r'>{}</td></tr>".format(
                    escape(item["label"]), escape(item["basis"]), escape(amount)
                )
            )
        values = {
            **data,
            "charge_rows": "".join(rows),
            "pay_status": "DEMO - NOT PAID",
        }
        html = TEMPLATE_PATH.read_text(encoding="utf-8")
        for key, value in values.items():
            if key in {"charge_items", "total_value", "charge_rows"}:
                continue
            html = html.replace("{{" + key + "}}", escape(str(value)))
        return html.replace("{{charge_rows}}", values["charge_rows"])

    def _claim(self, reservation_id: str, email: str) -> dict[str, Any]:
        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            existing = connection.execute(
                "SELECT * FROM bill_deliveries WHERE reservation_id = ?",
                (reservation_id,),
            ).fetchone()
            if existing and existing["status"] == "SENT":
                return {**dict(existing), "already_sent": True}
            if existing and existing["status"] == "SENDING":
                raise conflict("BILL_SEND_IN_PROGRESS", "This bill is already being sent")
            if existing:
                connection.execute(
                    """UPDATE bill_deliveries SET recipient_email = ?, status = 'SENDING',
                           attempt_count = attempt_count + 1, last_error = NULL, updated_at = ?
                       WHERE reservation_id = ?""",
                    (email, timestamp, reservation_id),
                )
                return {
                    **dict(existing), "recipient_email": email, "status": "SENDING",
                    "already_sent": False,
                }
            delivery_id = f"BILL-{uuid.uuid4().hex[:16].upper()}"
            invoice_no = f"ECU-{datetime.now(timezone.utc):%Y%m%d}-{reservation_id[-8:]}"
            connection.execute(
                """INSERT INTO bill_deliveries(
                       id, reservation_id, invoice_no, recipient_email, status,
                       attempt_count, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, 'SENDING', 1, ?, ?)""",
                (delivery_id, reservation_id, invoice_no, email, timestamp, timestamp),
            )
            return {
                "id": delivery_id, "reservation_id": reservation_id,
                "invoice_no": invoice_no, "recipient_email": email,
                "status": "SENDING", "already_sent": False,
            }

    async def send_bill(self, reservation_id: str, email: str, user: Any) -> dict[str, Any]:
        data = self._bill_data(reservation_id, user)
        delivery = self._claim(reservation_id, email)
        if delivery.get("already_sent"):
            return {
                "invoice_no": delivery["invoice_no"],
                "recipient_email": delivery["recipient_email"],
                "status": "SENT",
                "already_sent": True,
                "message": "The Demo Bill was already sent; no duplicate email was created.",
            }

        data.update(
            invoice_no=delivery["invoice_no"],
            issued_at=_time(now_iso()),
        )
        try:
            pdf = self.render_pdf(data)
            html = self.render_html(data)
            sender = (
                self.settings.smtp_sender_email
                or self.settings.smtp_username
                or "no-reply@ecurious.local"
            )
            message = EmailMessage()
            message["Subject"] = f"Ecurious Demo Bill {delivery['invoice_no']}"
            message["From"] = formataddr(("Ecurious Demo", sender))
            message["To"] = email
            message.set_content(
                "Your Ecurious Demo Bill is attached. All costs are simulated/estimated. "
                "This is not a tax invoice and no payment was processed."
            )
            message.add_alternative(html, subtype="html")
            message.add_attachment(
                pdf,
                maintype="application",
                subtype="pdf",
                filename=f"Ecurious-Demo-Bill-{delivery['invoice_no']}.pdf",
            )
            await asyncio.to_thread(_smtp_send, message, self.settings)
        except Exception as exc:
            with self.database.transaction(immediate=True) as connection:
                connection.execute(
                    """UPDATE bill_deliveries SET status = 'FAILED',
                           last_error = 'SMTP_DELIVERY_FAILED', updated_at = ?
                       WHERE reservation_id = ?""",
                    (now_iso(), reservation_id),
                )
            raise AppError(
                503,
                "BILL_EMAIL_FAILED",
                "The Demo Bill could not be sent. Check the email address or SMTP configuration and try again.",
            ) from exc

        timestamp = now_iso()
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                """UPDATE bill_deliveries SET status = 'SENT', pdf_sha256 = ?,
                       last_error = NULL, updated_at = ?, sent_at = ?
                   WHERE reservation_id = ?""",
                (hashlib.sha256(pdf).hexdigest(), timestamp, timestamp, reservation_id),
            )
        return {
            "invoice_no": delivery["invoice_no"],
            "recipient_email": email,
            "status": "SENT",
            "already_sent": False,
            "message": "Demo Bill sent successfully.",
        }
