"""Permit availability provider for Recreation.gov."""

from __future__ import annotations

from datetime import date, datetime

import httpx
from loguru import logger

from recsniper.models import AvailabilitySlot, Watch
from recsniper.utils import get_http_client, rate_limiter

from .base import BaseProvider

# Primary and fallback permit API endpoints
_PRIMARY_URL = "https://www.recreation.gov/api/permits/{facility_id}/availability"
_INYO_URL = "https://www.recreation.gov/api/permitinyo/{facility_id}/availability"


class PermitProvider(BaseProvider):
    """Checks permit availability via the Recreation.gov permits API.

    Some permits (e.g. Inyo National Forest) use a separate endpoint.
    The provider tries the primary endpoint first and falls back to the
    Inyo variant on 404 / unexpected errors.
    """

    _entrance_names_cache: dict[str, dict[str, str]] = {}
    _itinerary_permit_ids_cache: set[str] | None = None


    async def _get_entrance_names(self, client: httpx.AsyncClient, facility_id: str) -> dict[str, str]:
        """Fetch human-readable names for entrances from permitcontent endpoint."""
        if facility_id in self._entrance_names_cache:
            return self._entrance_names_cache[facility_id]

        url = f"https://www.recreation.gov/api/permitcontent/{facility_id}"
        try:
            await rate_limiter.acquire()
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                payload = data.get("payload", {})
                entrances = payload.get("entrances", [])
                names = {}
                for ent in entrances:
                    ent_id = ent.get("id")
                    ent_name = ent.get("name")
                    if ent_id and ent_name:
                        norm_id = "".join(c for c in str(ent_id) if c.isdigit())
                        names[norm_id] = str(ent_name)
                
                divisions = payload.get("divisions", {})
                if isinstance(divisions, dict):
                    for div_id, div in divisions.items():
                        div_name = div.get("name")
                        if div_name:
                            norm_id = "".join(c for c in str(div_id) if c.isdigit())
                            names[norm_id] = str(div_name)
                elif isinstance(divisions, list):
                    for div in divisions:
                        div_id = div.get("id")
                        div_name = div.get("name")
                        if div_id and div_name:
                            norm_id = "".join(c for c in str(div_id) if c.isdigit())
                            names[norm_id] = str(div_name)

                self._entrance_names_cache[facility_id] = names
                return names
        except Exception as exc:
            logger.debug("Failed to fetch permit content names for {}: {}", facility_id, exc)

        self._entrance_names_cache[facility_id] = {}
        return {}

    async def _is_itinerary_permit(self, client: httpx.AsyncClient, facility_id: str) -> bool:
        """Check if the facility ID is an itinerary-based permit."""
        # Hardcoded fallback for Olympic (facility ID 4098362)
        if facility_id == "4098362":
            return True

        if self._itinerary_permit_ids_cache is not None:
            return facility_id in self._itinerary_permit_ids_cache

        url = "https://www.recreation.gov/api/permitcontent/permitmapping"
        try:
            await rate_limiter.acquire()
            resp = await client.get(url)
            if resp.status_code == 200:
                data = resp.json()
                payload = data.get("payload", {})
                itinerary_ids = payload.get("itinerary_permit_ids", [])
                self._itinerary_permit_ids_cache = {str(i) for i in itinerary_ids}
                return facility_id in self._itinerary_permit_ids_cache
        except Exception as exc:
            logger.debug("Failed to fetch permit mapping: {}", exc)

        known_itineraries = {"4098362"}
        return facility_id in known_itineraries

    async def _check_itinerary_availability(
        self,
        client: httpx.AsyncClient,
        watch: Watch,
        entrance_names: dict[str, str],
        months: list[date],
    ) -> list[AvailabilitySlot]:
        """Query availability for itinerary-based permits (e.g. Olympic Wilderness)."""
        site_id_filter = watch.filters.get("site_id")
        site_ids_filter = watch.filters.get("site_ids")

        target_divisions = []
        if site_id_filter:
            target_divisions = ["".join(c for c in str(site_id_filter) if c.isdigit())]
        elif site_ids_filter:
            target_divisions = ["".join(c for c in str(sid) if c.isdigit()) for sid in site_ids_filter]
        else:
            # Fallback to checking all divisions in entrance names
            target_divisions = list(entrance_names.keys())

        slots = []
        for div_id in target_divisions:
            for m in months:
                url = f"https://www.recreation.gov/api/permititinerary/{watch.facility_id}/division/{div_id}/availability/month"
                params = {
                    "month": str(m.month),
                    "year": str(m.year),
                    "commercial": "false"
                }
                logger.debug("GET {} params={}", url, params)
                await rate_limiter.acquire()
                try:
                    resp = await client.get(url, params=params)
                    if resp.status_code == 200:
                        data = resp.json()
                        payload = data.get("payload", {})
                        bools = payload.get("bools", {})
                        quota_type_maps = payload.get("quota_type_maps", {})

                        div_name = entrance_names.get(div_id, div_id)

                        for date_str, is_avail in bools.items():
                            if not is_avail:
                                continue

                            slot_date = _parse_date(date_str)
                            if slot_date is None:
                                continue

                            remaining = 0
                            total = 0
                            show_walkup = False

                            for q_map in quota_type_maps.values():
                                if date_str in q_map:
                                    info = q_map[date_str]
                                    rem = info.get("remaining", 0)
                                    tot = info.get("total", 0)
                                    if rem > remaining:
                                        remaining = rem
                                    if tot > total:
                                        total = tot
                                    if info.get("show_walkup", False):
                                        show_walkup = True

                            if remaining == 0:
                                remaining = 1

                            slots.append(
                                AvailabilitySlot(
                                    facility_id=watch.facility_id,
                                    facility_name=watch.name,
                                    site_id=str(div_id),
                                    site_name=str(div_name),
                                    date=slot_date,
                                    status="Available",
                                    booking_url=f"https://www.recreation.gov/permits/{watch.facility_id}",
                                    raw_data={
                                        "remaining": remaining,
                                        "total": total,
                                        "is_walkup": show_walkup,
                                    }
                                )
                            )
                except Exception as exc:
                    logger.warning(
                        "Error checking itinerary availability for facility {} division {}: {}",
                        watch.facility_id,
                        div_id,
                        exc
                    )
        return slots

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    async def check_availability(self, watch: Watch) -> list[AvailabilitySlot]:
        """Query permit availability across the watch's date range.

        The permit API accepts ``start_date`` and ``end_date`` anchored
        to the first of a month.  We compute the minimal month span and
        issue a single request (with a fallback endpoint).

        Args:
            watch: The watch definition.

        Returns:
            A list of available permit slots.
        """
        months = self._months_in_range(watch.date_start, watch.date_end)
        start_month = months[0]
        end_month = months[-1]

        client = await get_http_client()

        # Pre-fetch entrance names for user-friendly notifications (especially for permitinyo)
        entrance_names = await self._get_entrance_names(client, watch.facility_id)

        # Check if it is an itinerary permit
        if await self._is_itinerary_permit(client, watch.facility_id):
            logger.info("Facility {} is an itinerary permit. Using itinerary API.", watch.facility_id)
            slots = await self._check_itinerary_availability(client, watch, entrance_names, months)
            slots = self._filter_by_date_range(slots, watch)
        else:
            # Try the primary endpoint first
            data = await self._fetch(
                client,
                _PRIMARY_URL.format(facility_id=watch.facility_id),
                start_month,
                end_month,
                watch,
            )

            # Fallback to Inyo endpoint if needed
            if data is None:
                logger.debug(
                    "Primary permit endpoint failed for {}; trying Inyo variant",
                    watch.facility_id,
                )
                data = await self._fetch(
                    client,
                    _INYO_URL.format(facility_id=watch.facility_id),
                    start_month,
                    end_month,
                    watch,
                )

            if data is None:
                logger.warning(
                    "All permit endpoints failed for {}", watch.facility_id
                )
                return []

            slots = self._parse_payload(data, watch, entrance_names)
            slots = self._filter_by_date_range(slots, watch)

        # Filter by specific entrance ID (site_id) if configured in watch filters
        site_id_filter = watch.filters.get("site_id")
        site_ids_filter = watch.filters.get("site_ids")
        if site_id_filter:
            norm_filter = "".join(c for c in str(site_id_filter) if c.isdigit())
            slots = [s for s in slots if "".join(c for c in s.site_id if c.isdigit()) == norm_filter]
        elif site_ids_filter:
            target_ids = {"".join(c for c in str(sid) if c.isdigit()) for sid in site_ids_filter}
            slots = [s for s in slots if "".join(c for c in s.site_id if c.isdigit()) in target_ids]

        logger.debug(
            "Permit {} — {} available slot(s) after filtering",
            watch.facility_id,
            len(slots),
        )
        return slots

    def build_deep_link(self, watch: Watch) -> str:
        return f"https://www.recreation.gov/permits/{watch.facility_id}"

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch(
        self,
        client: httpx.AsyncClient,
        url: str,
        start_month: date,
        end_month: date,
        watch: Watch,
    ) -> dict | None:
        """Issue a GET request to the given permit URL.

        Returns the parsed JSON dict on success, or ``None`` on any
        failure (HTTP error, rate-limit, bad JSON, etc.).
        """
        import calendar
        last_day = calendar.monthrange(end_month.year, end_month.month)[1]

        if "permitinyo" in url:
            params = {
                "start_date": f"{start_month.isoformat()}T00:00:00.000Z",
                "end_date": f"{end_month.year:04d}-{end_month.month:02d}-{last_day:02d}"
            }
        else:
            params = {
                "start_date": f"{start_month.isoformat()}T00:00:00.000Z",
                "end_date": f"{end_month.year:04d}-{end_month.month:02d}-{last_day:02d}T00:00:00.000Z",
            }

        logger.debug("GET {} params={}", url, params)

        await rate_limiter.acquire()

        try:
            response = await client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning(
                "HTTP error fetching permit {}: {}", watch.facility_id, exc
            )
            return None

        if response.status_code == 429:
            logger.warning(
                "Rate-limited (429) while querying permit {}",
                watch.facility_id,
            )
            return None

        if response.status_code == 404:
            logger.debug(
                "404 for permit endpoint {} (facility {})",
                url,
                watch.facility_id,
            )
            return None

        if response.status_code != 200:
            logger.warning(
                "Unexpected status {} for permit {} at {}",
                response.status_code,
                watch.facility_id,
                url,
            )
            return None

        try:
            return response.json()
        except ValueError:
            logger.error(
                "Invalid JSON in permit response for {}", watch.facility_id
            )
            return None

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_payload(
        data: dict,
        watch: Watch,
        entrance_names: dict[str, str] | None = None,
    ) -> list[AvailabilitySlot]:
        """Parse the permit JSON payload into ``AvailabilitySlot`` objects.

        Handles both standard permits and permitinyo date-first permit layouts.
        """
        # Normalise: prefer data["payload"], fall back to data itself
        payload: dict = data.get("payload", data)

        # Guard against the payload being a list or other non-dict type
        if not isinstance(payload, dict):
            logger.warning("Permit payload is not a dict — skipping")
            return []

        # If payload contains 'availability' sub-key (standard format), extract it
        if "availability" in payload and isinstance(payload["availability"], dict):
            payload = payload["availability"]

        slots: list[AvailabilitySlot] = []

        # Check if this is the permitinyo date-first layout where keys are dates
        is_date_first = False
        for key in payload.keys():
            if _parse_date(key) is not None:
                is_date_first = True
                break

        if is_date_first:
            for date_str, entrances_dict in payload.items():
                if not isinstance(entrances_dict, dict):
                    continue
                slot_date = _parse_date(date_str)
                if slot_date is None:
                    continue
                for entrance_id, day_info in entrances_dict.items():
                    if not isinstance(day_info, dict):
                        continue
                    remaining = day_info.get("remaining", 0)
                    try:
                        remaining_int = int(remaining)
                    except (TypeError, ValueError):
                        remaining_int = 0
                    if remaining_int <= 0:
                        continue

                    # Try to map to the human-readable entrance name
                    entrance_name = entrance_id
                    norm_id = "".join(c for c in str(entrance_id) if c.isdigit())
                    if entrance_names and norm_id in entrance_names:
                        entrance_name = entrance_names[norm_id]

                    slots.append(
                        AvailabilitySlot(
                            facility_id=watch.facility_id,
                            facility_name=watch.name,
                            site_id=str(entrance_id),
                            site_name=str(entrance_name),
                            date=slot_date,
                            status="Available",
                            booking_url=f"https://www.recreation.gov/permits/{watch.facility_id}",
                            raw_data={
                                "remaining": remaining_int,
                                "total": day_info.get("total"),
                                "is_walkup": day_info.get("is_walkup", False),
                            },
                        )
                    )
        else:
            for entrance_id, entrance_info in payload.items():
                # Skip metadata keys that aren't entrance dicts
                if not isinstance(entrance_info, dict):
                    continue

                date_availability: dict = entrance_info.get(
                    "date_availability", {}
                )
                if not isinstance(date_availability, dict):
                    continue

                entrance_name = entrance_info.get(
                    "entrance_name",
                    entrance_info.get("trail_name")
                )
                norm_id = "".join(c for c in str(entrance_id) if c.isdigit())
                if not entrance_name or entrance_name == str(entrance_id):
                    if entrance_names and norm_id in entrance_names:
                        entrance_name = entrance_names[norm_id]
                if not entrance_name:
                    entrance_name = str(entrance_id)

                for date_str, day_info in date_availability.items():
                    if not isinstance(day_info, dict):
                        continue

                    remaining = day_info.get("remaining", 0)
                    try:
                        remaining_int = int(remaining)
                    except (TypeError, ValueError):
                        remaining_int = 0

                    if remaining_int <= 0:
                        continue

                    slot_date = _parse_date(date_str)
                    if slot_date is None:
                        logger.debug("Skipping unparseable permit date: {}", date_str)
                        continue

                    slots.append(
                        AvailabilitySlot(
                            facility_id=watch.facility_id,
                            facility_name=watch.name,
                            site_id=str(entrance_id),
                            site_name=str(entrance_name),
                            date=slot_date,
                            status="Available",
                            booking_url=(
                                f"https://www.recreation.gov/permits/"
                                f"{watch.facility_id}"
                            ),
                            raw_data={
                                "remaining": remaining_int,
                                "total": day_info.get("total"),
                                "is_walkup": day_info.get("is_walkup", False),
                            },
                        )
                    )

        return slots


# ------------------------------------------------------------------
# Module-level utilities
# ------------------------------------------------------------------


def _parse_date(value: str) -> date | None:
    """Best-effort date parsing for the various formats Recreation.gov uses."""
    # Try bare date first (YYYY-MM-DD)
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue

    # ISO with timezone offset
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (ValueError, TypeError):
        return None
