"""Test unit per tools/google_maps.py — mock HTTPX con respx."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from tools.google_maps import MapsAPIError, get_place_details, search_businesses

# ---------------------------------------------------------------------------
# Payload di risposta Google Places API (New) — minimale ma strutturalmente corretto
# ---------------------------------------------------------------------------

_SEARCH_RESPONSE = {
    "places": [
        {
            "id": "ChIJtest001",
            "displayName": {"text": "Bar Roma Test"},
            "formattedAddress": "Via Roma 1, Roma RM, Italy",
            "addressComponents": [
                {"types": ["locality"], "longText": "Roma"},
                {"types": ["administrative_area_level_1"], "longText": "Lazio"},
                {"types": ["country"], "shortText": "IT"},
            ],
            "location": {"latitude": 41.9028, "longitude": 12.4964},
            "rating": 4.2,
            "userRatingCount": 87,
            "primaryTypeDisplayName": {"text": "Bar"},
            "websiteUri": None,
            "nationalPhoneNumber": "+39 06 12345678",
        }
    ]
}

_DETAIL_RESPONSE = {
    "id": "ChIJtest001",
    "displayName": {"text": "Bar Roma Test"},
    "formattedAddress": "Via Roma 1, Roma RM, Italy",
    "addressComponents": [
        {"types": ["locality"], "longText": "Roma"},
        {"types": ["administrative_area_level_1"], "longText": "Lazio"},
        {"types": ["country"], "shortText": "IT"},
    ],
    "location": {"latitude": 41.9028, "longitude": 12.4964},
    "rating": 4.2,
    "userRatingCount": 87,
    "primaryTypeDisplayName": {"text": "Bar"},
    "websiteUri": None,
    "nationalPhoneNumber": "+39 06 12345678",
}


# ---------------------------------------------------------------------------
# search_businesses
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_search_businesses_happy_path():
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=Response(200, json=_SEARCH_RESPONSE)
    )

    results = await search_businesses(
        query="bar Roma",
        location="Roma, Italia",
        radius_km=5,
        max_results=5,
    )

    assert len(results) == 1
    place = results[0]
    assert place["google_place_id"] == "ChIJtest001"
    assert place["business_name"] == "Bar Roma Test"
    assert place["city"] == "Roma"
    assert place["country"] == "IT"
    assert place["google_rating"] == 4.2


@pytest.mark.asyncio
@respx.mock
async def test_search_businesses_empty_results():
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=Response(200, json={"places": []})
    )

    results = await search_businesses(
        query="macchinari industriali Agrigento",
        location="Agrigento, Italia",
        radius_km=10,
    )

    assert results == []


@pytest.mark.asyncio
@respx.mock
async def test_search_businesses_http_error_raises_maps_api_error():
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        return_value=Response(403, json={"error": {"message": "API key invalid"}})
    )

    with pytest.raises(MapsAPIError) as exc_info:
        await search_businesses("bar Roma", "Roma, Italia")

    assert exc_info.value.code == "tool_maps_api_error"


@pytest.mark.asyncio
@respx.mock
async def test_search_businesses_network_error():
    import httpx
    respx.post("https://places.googleapis.com/v1/places:searchText").mock(
        side_effect=httpx.ConnectError("Connection refused")
    )

    with pytest.raises(MapsAPIError):
        await search_businesses("bar Roma", "Roma, Italia")


# ---------------------------------------------------------------------------
# get_place_details
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
@respx.mock
async def test_get_place_details_happy_path():
    respx.get(
        "https://places.googleapis.com/v1/places/ChIJtest001"
    ).mock(return_value=Response(200, json=_DETAIL_RESPONSE))

    result = await get_place_details("ChIJtest001")

    assert result["google_place_id"] == "ChIJtest001"
    assert result["business_name"] == "Bar Roma Test"
    assert result["city"] == "Roma"


@pytest.mark.asyncio
@respx.mock
async def test_get_place_details_not_found():
    respx.get(
        "https://places.googleapis.com/v1/places/ChIJnotfound"
    ).mock(return_value=Response(404, json={"error": {"message": "Not found"}}))

    with pytest.raises(MapsAPIError):
        await get_place_details("ChIJnotfound")
