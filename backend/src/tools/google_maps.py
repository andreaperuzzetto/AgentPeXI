from __future__ import annotations

import asyncio
import os

import httpx
import structlog

from tools import AgentToolError

log = structlog.get_logger()

# Rate limiter: max 100 concurrent requests
_semaphore = asyncio.Semaphore(100)

_PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
_PLACE_DETAILS_URL = "https://places.googleapis.com/v1/places/{place_id}"
_GEOCODE_URL = "https://maps.googleapis.com/maps/api/geocode/json"


class MapsAPIError(AgentToolError):
    def __init__(self, message: str = "") -> None:
        super().__init__(code="tool_maps_api_error", message=message)


def _api_key() -> str:
    return os.environ["GOOGLE_MAPS_API_KEY"]


def _parse_place(place: dict) -> dict:
    location = place.get("location", {})
    address_components = place.get("addressComponents", [])

    city = ""
    region = ""
    country = "IT"
    for comp in address_components:
        types = comp.get("types", [])
        if "locality" in types:
            city = comp.get("longText", "")
        elif "administrative_area_level_1" in types:
            region = comp.get("longText", "")
        elif "country" in types:
            country = comp.get("shortText", "IT")

    return {
        "google_place_id": place.get("id", ""),
        "business_name": place.get("displayName", {}).get("text", ""),
        "address": place.get("formattedAddress", ""),
        "city": city,
        "region": region,
        "country": country,
        "latitude": location.get("latitude"),
        "longitude": location.get("longitude"),
        "google_rating": place.get("rating"),
        "google_review_count": place.get("userRatingCount"),
        "google_category": (place.get("primaryTypeDisplayName") or {}).get("text"),
        "website_url": place.get("websiteUri"),
        "phone": place.get("nationalPhoneNumber"),
    }


async def search_businesses(
    query: str,
    location: str,
    radius_km: int = 10,
    max_results: int = 20,
) -> list[dict]:
    full_query = f"{query} {location}"
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": _api_key(),
        "X-Goog-FieldMask": (
            "places.id,places.displayName,places.formattedAddress,"
            "places.addressComponents,places.location,places.rating,"
            "places.userRatingCount,places.primaryTypeDisplayName,"
            "places.websiteUri,places.nationalPhoneNumber"
        ),
    }
    body = {
        "textQuery": full_query,
        "maxResultCount": min(max_results, 20),
        "locationBias": {
            "circle": {
                "center": {"latitude": 0, "longitude": 0},
                "radius": radius_km * 1000.0,
            }
        },
    }
    async with _semaphore:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.post(
                    _PLACES_SEARCH_URL, json=body, headers=headers
                )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MapsAPIError(f"HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except Exception as exc:
            raise MapsAPIError(str(exc)) from exc

    data = response.json()
    places = data.get("places", [])
    log.info("maps.search_businesses", count=len(places), query_location=location)
    return [_parse_place(p) for p in places]


async def get_place_details(google_place_id: str) -> dict:
    url = _PLACE_DETAILS_URL.format(place_id=google_place_id)
    field_mask = (
        "id,displayName,formattedAddress,addressComponents,location,"
        "rating,userRatingCount,primaryTypeDisplayName,websiteUri,"
        "nationalPhoneNumber,regularOpeningHours,types"
    )
    headers = {
        "X-Goog-Api-Key": _api_key(),
        "X-Goog-FieldMask": field_mask,
    }
    async with _semaphore:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(url, headers=headers)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MapsAPIError(f"HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except Exception as exc:
            raise MapsAPIError(str(exc)) from exc

    place = response.json()
    log.info("maps.get_place_details", place_id=google_place_id)
    result = _parse_place(place)
    result["opening_hours"] = place.get("regularOpeningHours")
    result["types"] = place.get("types", [])
    return result


async def geocode_address(address: str) -> tuple[float, float] | None:
    params = {"address": address, "key": _api_key()}
    async with _semaphore:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                response = await client.get(_GEOCODE_URL, params=params)
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MapsAPIError(f"HTTP {exc.response.status_code}: {exc.response.text}") from exc
        except Exception as exc:
            raise MapsAPIError(str(exc)) from exc

    data = response.json()
    results = data.get("results", [])
    if not results:
        return None
    loc = results[0]["geometry"]["location"]
    return (loc["lat"], loc["lng"])
