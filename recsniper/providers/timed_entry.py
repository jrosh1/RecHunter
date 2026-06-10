"""Timed-entry availability provider for Recreation.gov."""

from __future__ import annotations

from datetime import date

import httpx
from loguru import logger

from recsniper.models import AvailabilitySlot, Watch
from recsniper.utils import get_http_client, rate_limiter

from .base import BaseProvider

_BASE_URL = (
    "https://www.recreation.gov/api/timedentry/availability/facility"
)
_TICKET_BASE_URL = (
    "https://www.recreation.gov/api/ticket/availability/facility"
)


class TimedEntryProvider(BaseProvider):
    """Checks timed-entry ticket availability via the Recreation.gov API.

    Unlike campgrounds and permits, the timed-entry API must be queried
    **per date**, so this provider iterates over every day in the watch's
    range and respects the rate limiter between requests.
    """

    # Class-level cache to map tour_id -> parent_facility_id to avoid redundant lookups
    _tour_to_facility_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def check_availability(self, watch: Watch) -> list[AvailabilitySlot]:
        """Query timed-entry availability for each date in the range.

        Args:
            watch: The watch definition.

        Returns:
            A list of available timed-entry slots.
        """
        facility_id = watch.facility_id
        tour_filter_id: str | None = watch.filters.get("site_id")
        is_tour = False
        client = await get_http_client()

        # Determine if we have a tour to resolve or filter on
        target_id = tour_filter_id or facility_id

        # If we know this ID is a tour ID, resolve it to the parent facility ID
        if target_id in self._tour_to_facility_cache:
            parent_id = self._tour_to_facility_cache[target_id]
            if parent_id != target_id:
                if not tour_filter_id:
                    tour_filter_id = target_id
                facility_id = parent_id
                is_tour = True
            else:
                # Target is parent or a ticket facility
                is_tour = True
        elif len(target_id) >= 7 and target_id.startswith("100") or target_id.isdigit():
            # Looks like a tour ID (tours on recreation.gov are usually 7-8 digits and often start with 100)
            try:
                await rate_limiter.acquire()
                tour_url = f"https://www.recreation.gov/api/ticket/tour/{target_id}"
                resp = await client.get(tour_url)
                if resp.status_code == 200:
                    tour_data = resp.json()
                    parent_id = tour_data.get("facility_id")
                    if parent_id and parent_id != target_id:
                        logger.info("Resolved tour ID {} to parent facility ID {}", target_id, parent_id)
                        self._tour_to_facility_cache[target_id] = parent_id
                        if not tour_filter_id:
                            tour_filter_id = target_id
                        facility_id = parent_id
                        is_tour = True
                    else:
                        self._tour_to_facility_cache[target_id] = target_id
                        is_tour = True
                else:
                    # Not a tour or endpoint failed, treat as facility
                    self._tour_to_facility_cache[target_id] = target_id
            except Exception as exc:
                logger.debug("Failed to resolve tour ID {}: {}", target_id, exc)
                self._tour_to_facility_cache[target_id] = target_id

        dates = self._iter_dates(watch.date_start, watch.date_end)
        all_slots: list[AvailabilitySlot] = []

        for query_date in dates:
            slots = await self._fetch_date(client, watch, query_date, facility_id, is_tour)
            all_slots.extend(slots)

        # Filter by specific tour ID if this watch has a site_id/tour filter
        site_ids_filter = watch.filters.get("site_ids")
        if tour_filter_id:
            all_slots = [s for s in all_slots if s.site_id == tour_filter_id]
        elif site_ids_filter:
            target_ids = {str(sid) for sid in site_ids_filter}
            all_slots = [s for s in all_slots if s.site_id in target_ids]

        logger.debug(
            "TimedEntry {} — {} available slot(s) across {} date(s)",
            watch.facility_id,
            len(all_slots),
            len(dates),
        )
        return all_slots

    def build_deep_link(self, watch: Watch) -> str:
        return f"https://www.recreation.gov/timed-entry/{watch.facility_id}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_date(
        self,
        client: httpx.AsyncClient,
        watch: Watch,
        query_date: date,
        facility_id: str | None = None,
        is_tour: bool = False,
    ) -> list[AvailabilitySlot]:
        """Fetch timed-entry availability for a single date.

        Args:
            client: Shared ``httpx.AsyncClient``.
            watch: Watch definition.
            query_date: The specific date to query.
            facility_id: Resolved parent facility ID if watch.facility_id is a tour.
            is_tour: Whether this is a tour/ticket type requiring the ticket API path.

        Returns:
            Parsed ``AvailabilitySlot`` objects for every available
            time-slot on the given date.
        """
        fid = facility_id or watch.facility_id
        base_url = _TICKET_BASE_URL if is_tour else _BASE_URL
        url = f"{base_url}/{fid}"
        params = {"date": query_date.isoformat()}

        logger.debug("GET {} params={}", url, params)

        await rate_limiter.acquire()

        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "HTTP error fetching timed entry {} on {}: {}",
                fid,
                query_date,
                exc,
            )
            return []

        if response.status_code == 429:
            logger.warning(
                "Rate-limited (429) while querying timed entry {} on {}",
                fid,
                query_date,
            )
            return []

        if response.status_code != 200:
            logger.warning(
                "Unexpected status {} for timed entry {} on {}",
                response.status_code,
                fid,
                query_date,
            )
            return []

        try:
            data = response.json()
        except ValueError:
            logger.error(
                "Invalid JSON in timed-entry response for {} on {}",
                fid,
                query_date,
            )
            return []

        return self._parse_facility_map(data, watch, query_date)

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_facility_map(
        data: dict | list,
        watch: Watch,
        query_date: date,
    ) -> list[AvailabilitySlot]:
        """Parse the timed-entry JSON into ``AvailabilitySlot`` objects.

        Handles both list-style response and the dict-style response wrapped in "payload".
        """
        slots: list[AvailabilitySlot] = []

        if isinstance(data, list):
            # List-style response (e.g. from `/api/timedentry/availability/facility/{id}?date=YYYY-MM-DD`)
            for item in data:
                if not isinstance(item, dict):
                    continue
                
                status = item.get("status", "").upper()
                if status == "CLOSED":
                    continue
                
                inventory = item.get("inventory_count", {})
                reservations = item.get("reservation_count", {})
                
                remaining_int = 0
                if isinstance(inventory, dict) and isinstance(reservations, dict):
                    for key, total in inventory.items():
                        try:
                            reserved = reservations.get(key, 0)
                            remaining_int += max(0, int(total) - int(reserved))
                        except (TypeError, ValueError):
                            continue

                # Check if it has active remaining spots or is marked open
                is_available = (status == "OPEN" and remaining_int > 0)
                if not is_available:
                    continue

                tour_id = item.get("tour_id", "")
                tour_time = item.get("tour_time", "")
                
                # Format a user-friendly time label
                time_label = f"{tour_time[:2]}:{tour_time[2:]}" if len(tour_time) == 4 and tour_time.isdigit() else tour_time

                slots.append(
                    AvailabilitySlot(
                        facility_id=watch.facility_id,
                        facility_name=watch.name,
                        site_id=str(tour_id),
                        site_name=f"Tour {tour_id} ({time_label})",
                        date=query_date,
                        status="Available",
                        booking_url=f"https://www.recreation.gov/timed-entry/{watch.facility_id}",
                        raw_data={
                            "tour_id": tour_id,
                            "tour_time": tour_time,
                            "remaining_count": remaining_int,
                            "original_status": status,
                        },
                    )
                )
            return slots

        if isinstance(data, dict):
            payload = data.get("payload", {})
            if not isinstance(payload, dict):
                logger.warning("Timed-entry payload is not a dict — skipping")
                return []

            facility_map: dict = payload.get("facility_availability_map", {})
            if not isinstance(facility_map, dict):
                return []

            for tour_id, tour_info in facility_map.items():
                if not isinstance(tour_info, dict):
                    continue

                tour_name = tour_info.get("tour_name", str(tour_id))
                availability_map: dict = tour_info.get("availability_map", {})
                if not isinstance(availability_map, dict):
                    continue

                for slot_id, slot_info in availability_map.items():
                    if not isinstance(slot_info, dict):
                        continue

                    status = slot_info.get("status", "")
                    remaining = slot_info.get("remaining_count", 0)

                    try:
                        remaining_int = int(remaining)
                    except (TypeError, ValueError):
                        remaining_int = 0

                    is_available = (
                        status.lower() == "available" or remaining_int > 0
                    )
                    if not is_available:
                        continue

                    start_time = slot_info.get("start_time", "")
                    end_time = slot_info.get("end_time", "")
                    time_label = (
                        f"{start_time}–{end_time}" if start_time else slot_id
                    )

                    slots.append(
                        AvailabilitySlot(
                            facility_id=watch.facility_id,
                            facility_name=watch.name,
                            site_id=str(tour_id),
                            site_name=f"{tour_name} ({time_label})",
                            date=query_date,
                            status="Available",
                            booking_url=f"https://www.recreation.gov/timed-entry/{watch.facility_id}",
                            raw_data={
                                "tour_id": tour_id,
                                "slot_id": slot_id,
                                "start_time": start_time,
                                "end_time": end_time,
                                "remaining_count": remaining_int,
                                "original_status": status,
                            },
                        )
                    )

        return slots
