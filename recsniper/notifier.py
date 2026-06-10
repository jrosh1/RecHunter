"""
RecHunter Notifier
==================

SMS notifications via the Gmail SMTP → carrier email-to-SMS gateway.

Usage::

    notifier = SMSNotifier(
        gmail_address="you@gmail.com",
        gmail_app_password="abcd efgh ijkl mnop",
        phone_number="5551234567",
        carrier_gateway="tmomail.net",
    )
    ok = await notifier.send_test()
"""

from __future__ import annotations

import asyncio
import smtplib
from email.mime.text import MIMEText
from functools import partial
from typing import Optional

from loguru import logger

from recsniper.models import AvailabilitySlot, Watch
from recsniper.utils import build_booking_url

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SMTP_HOST = "smtp.gmail.com"
_SMTP_PORT = 587  # STARTTLS


# ---------------------------------------------------------------------------
# SMS Notifier
# ---------------------------------------------------------------------------

class SMSNotifier:
    """Send SMS messages via the Gmail SMTP → carrier email-to-SMS gateway.

    This wraps the blocking :mod:`smtplib` calls in
    :func:`asyncio.get_event_loop().run_in_executor` so they are safe to
    use from an async event loop.

    Parameters
    ----------
    gmail_address : str
        The Gmail address to send *from*.
    gmail_app_password : str
        A Gmail **App Password** (16-char, no spaces needed).
    phone_number : str
        Recipient phone number (digits only, e.g. ``"5551234567"``).
    carrier_gateway : str
        The carrier's email-to-SMS gateway domain (e.g. ``"tmomail.net"``
        for T-Mobile, ``"vtext.com"`` for Verizon).
    """

    def __init__(
        self,
        gmail_address: str,
        gmail_app_password: str,
        phone_number: str,
        carrier_gateway: str = "tmomail.net",
    ) -> None:
        self.gmail_address = gmail_address
        self.gmail_app_password = gmail_app_password
        self.phone_number = phone_number
        self.carrier_gateway = carrier_gateway

    # -- Public API ---------------------------------------------------------

    async def send_sms(self, message: str) -> bool:
        """Send a plain-text SMS via the email-to-SMS gateway, WhatsApp, or Telegram."""
        if self.carrier_gateway == "whatsapp":
            return await self._send_whatsapp(message)
        elif self.carrier_gateway == "telegram":
            return await self._send_telegram(message)
        recipient = f"{self.phone_number}@{self.carrier_gateway}"
        return await self._send_email(recipient, message)

    async def _send_whatsapp(self, message: str) -> bool:
        """Send a WhatsApp notification using CallMeBot API."""
        import httpx
        phone = self.phone_number
        if not phone.startswith("+"):
            if phone.startswith("1") and len(phone) == 11:
                phone = phone[1:]
            phone = f"+1{phone}"

        url = "https://api.callmebot.com/whatsapp.php"
        params = {
            "phone": phone,
            "text": message,
        }
        if self.gmail_app_password:
            params["apikey"] = self.gmail_app_password

        logger.info("Sending WhatsApp alert via CallMeBot to {}", phone)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    logger.info("WhatsApp message sent successfully.")
                    return True
                else:
                    logger.error("CallMeBot returned status {}: {}", resp.status_code, resp.text)
                    return False
        except Exception as exc:
            logger.error("Failed to send WhatsApp message: {}", exc)
            return False

    async def _send_telegram(self, message: str) -> bool:
        """Send a Telegram notification using CallMeBot API."""
        import httpx
        user = self.phone_number
        if not user.startswith("@"):
            user = f"@{user}"

        url = "https://api.callmebot.com/text.php"
        params = {
            "user": user,
            "text": message,
        }
        if self.gmail_app_password:
            params["apikey"] = self.gmail_app_password

        logger.info("Sending Telegram alert via CallMeBot to {}", user)
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(url, params=params)
                if resp.status_code == 200:
                    logger.info("Telegram message sent successfully.")
                    return True
                else:
                    logger.error("CallMeBot Telegram returned status {}: {}", resp.status_code, resp.text)
                    return False
        except Exception as exc:
            logger.error("Failed to send Telegram message: {}", exc)
            return False

    async def send_availability_alert(
        self,
        watch: Watch,
        slots: list[AvailabilitySlot],
    ) -> bool:
        """Format and send a detailed availability alert.

        Groups slots by site/trailhead and formats dates for each.
        """
        if not slots:
            return False

        # Use the first slot's facility name, falling back to watch name
        facility_name = slots[0].facility_name or watch.name

        # Group slots by site/trailhead name
        by_site: dict[str, list] = {}
        for s in slots:
            site_name = s.site_name or f"Site {s.site_id}"
            by_site.setdefault(site_name, []).append(s.date)

        site_lines = []
        for site, dates in sorted(by_site.items()):
            unique_dates = sorted(set(dates))
            date_str = self._format_date_range(unique_dates)
            site_lines.append(f"📍 {site}: {date_str}")

        # Limit lines to prevent overflow
        max_lines = 6
        if len(site_lines) > max_lines:
            truncated = site_lines[:max_lines]
            truncated.append(f"… and {len(site_lines) - max_lines} more")
            details = "\n".join(truncated)
        else:
            details = "\n".join(site_lines)

        # Build reservation type specific book link
        if watch.reservation_type == "timed_entry" and watch.facility_id.startswith("100"):
            from recsniper.providers.timed_entry import TimedEntryProvider
            parent_id = TimedEntryProvider._tour_to_facility_cache.get(watch.facility_id)
            if parent_id:
                link = f"https://www.recreation.gov/ticket/{parent_id}/ticket/{watch.facility_id}"
            else:
                link = build_booking_url(watch.facility_id, watch.reservation_type)
        else:
            link = build_booking_url(watch.facility_id, watch.reservation_type)

        message = (
            f"🌲 RecHunter Alert: {facility_name}\n"
            f"{details}\n"
            f"🔗 Book: {link}"
        )

        logger.info("Sending availability alert for watch '{}': {} slots", watch.name, len(slots))
        return await self.send_sms(message)

    async def send_test(self) -> bool:
        """Send a test notification to verify the SMS pipeline.

        Returns
        -------
        bool
            ``True`` if the test message was sent successfully.
        """
        message = "RecHunter test notification - SMS pipeline working! 🌲"
        logger.info("Sending test SMS to {}@{}", self.phone_number, self.carrier_gateway)
        return await self.send_sms(message)

    # -- Internal helpers ---------------------------------------------------

    async def _send_email(self, recipient: str, body: str) -> bool:
        """Send an email via Gmail SMTP in a thread executor.

        Parameters
        ----------
        recipient : str
            Full email address (e.g. ``"5551234567@tmomail.net"``).
        body : str
            Plain-text message body.

        Returns
        -------
        bool
            ``True`` on success, ``False`` on failure (logged).
        """
        msg = MIMEText(body)
        msg["From"] = self.gmail_address
        msg["To"] = recipient
        # SMS gateways typically ignore the subject, but set it anyway
        msg["Subject"] = "RecHunter Alert"

        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None,
                partial(self._blocking_send, recipient, msg.as_string()),
            )
            logger.info("SMS sent successfully to {}", recipient)
            return True
        except smtplib.SMTPAuthenticationError as exc:
            logger.error(
                "SMTP authentication failed — check GMAIL_APP_PASSWORD: {}", exc
            )
            return False
        except smtplib.SMTPException as exc:
            logger.error("SMTP error sending to {}: {}", recipient, exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error sending SMS to {}: {}", recipient, exc)
            return False

    def _blocking_send(self, recipient: str, msg_string: str) -> None:
        """Synchronous SMTP send (runs inside a thread executor)."""
        with smtplib.SMTP(_SMTP_HOST, _SMTP_PORT) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(self.gmail_address, self.gmail_app_password)
            server.sendmail(self.gmail_address, recipient, msg_string)

    @staticmethod
    def _format_date_range(dates: list) -> str:
        """Format a sorted list of dates into a compact string.

        Examples:
            [Dec 4]         → "Dec 4"
            [Dec 4, Dec 5]  → "Dec 4-5"
            [Dec 4, Dec 7]  → "Dec 4, Dec 7"
            [Dec 4, Dec 5, Dec 6] → "Dec 4-6"
        """
        if not dates:
            return ""

        sorted_dates = sorted(dates)

        def fmt_date(d):
            if not hasattr(d, "strftime"):
                return str(d)
            s = d.strftime("%b %d")
            parts = s.split()
            if len(parts) == 2:
                return f"{parts[0]} {int(parts[1])}"
            return s

        if len(sorted_dates) == 1:
            return fmt_date(sorted_dates[0])

        first = sorted_dates[0]
        last = sorted_dates[-1]
        expected_count = (last - first).days + 1

        try:
            if len(sorted_dates) == expected_count:
                # Contiguous range
                if first.month == last.month:
                    return f"{first.strftime('%b')} {first.day}-{last.day}"
                else:
                    return f"{first.strftime('%b')} {first.day}-{last.strftime('%b')} {last.day}"
            else:
                # Non-contiguous — show first few
                formatted = [fmt_date(d) for d in sorted_dates[:3]]
                result = ", ".join(formatted)
                if len(sorted_dates) > 3:
                    result += f" +{len(sorted_dates) - 3} more"
                return result
        except Exception:
            # Fallback for any edge case
            return f"{first} – {last}"
