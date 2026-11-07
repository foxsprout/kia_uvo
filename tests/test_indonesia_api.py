"""Tests for Hyundai Bluelink Indonesia (AP) API implementation."""

from __future__ import annotations

import base64
import datetime as dt
from typing import Any
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions
import hyundai_kia_connect_api.const as api_const
from hyundai_kia_connect_api.const import (
    CHARGE_PORT_ACTION,
    ENGINE_TYPES,
    SEAT_LOCATION,
    VEHICLE_LOCK_ACTION,
)
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.Vehicle import Vehicle
import pytest

from custom_components.kia_uvo.api_id import (
    REGION_INDONESIA,
    REGION_INDONESIA_ID,
    HyundaiBlueLinkApiID,
    _extract_code_from_url_or_input,
    register_indonesia_region,
)
from custom_components.kia_uvo.const import REGIONS


def _int_to_b64url(val: int) -> str:
    length = (val.bit_length() + 7) // 8
    raw = val.to_bytes(length, byteorder="big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_mock_response(
    json_data: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    url: str = "",
    cookies: dict[str, str] | None = None,
) -> MagicMock:
    resp = MagicMock()
    if json_data is not None:
        resp.json.return_value = json_data
    else:
        resp.json.side_effect = ValueError("No JSON")
    resp.headers = headers or {}
    resp.url = url
    resp.cookies.get_dict.return_value = cookies or {}
    return resp


def test_region_registration() -> None:
    """Indonesia region (10) is registered alongside all original regions (1-9)."""
    register_indonesia_region()
    for region_id in range(1, 10):
        assert region_id in REGIONS
    assert REGIONS[REGION_INDONESIA_ID] == REGION_INDONESIA
    assert api_const.REGIONS[REGION_INDONESIA_ID] == REGION_INDONESIA

    impl = VehicleManager.get_implementation_by_region_brand(
        REGION_INDONESIA_ID, 2, "en"
    )
    assert isinstance(impl, HyundaiBlueLinkApiID)
    assert impl.BASE_URL == "apigw-ccs-ap.hyundai.com"

    # Other regions still resolve through the original factory
    eu_impl = VehicleManager.get_implementation_by_region_brand(1, 2, "en")
    assert not isinstance(eu_impl, HyundaiBlueLinkApiID)
    au_impl = VehicleManager.get_implementation_by_region_brand(5, 2, "en")
    assert not isinstance(au_impl, HyundaiBlueLinkApiID)
    assert au_impl.BASE_URL == "au-apigw.ccs.hyundai.com.au:8080"


def test_indonesia_constants_and_headers() -> None:
    """Verify APK constants, stamp XOR keystream, RHD seat location, and headers."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")
    assert api.BASE_URL == "apigw-ccs-ap.hyundai.com"
    assert api.IDP_URL == "https://idpconnect-ap.hyundai.com"
    assert api.APP_ID == "4250ad16-2a86-4cd4-8e33-7a6f4f265f94"
    assert api.CCSP_SERVICE_ID == "a3f38b3c-5948-48e9-97ee-1176c2662292"
    assert api.data_timezone == ZoneInfo("Asia/Jakarta")

    stamp = api._get_stamp()
    raw_stamp = base64.b64decode(stamp)
    decrypted = bytes(b1 ^ b2 for b1, b2 in zip(api.CFB, raw_stamp)).decode("utf-8")
    assert decrypted.startswith(f"{api.APP_ID}:")

    vehicle = Vehicle(
        id="veh-1",
        name="IONIQ 5",
        model="IONIQ 5",
        registration_date="20240101",
        VIN="KM800000000000001",
        timezone=api.data_timezone,
        ccu_ccs2_protocol_support=1,
    )
    assert api._get_drv_seat_loc(vehicle) == SEAT_LOCATION.RIGHT

    token = Token(
        username="user@example.com",
        password="pw",
        access_token="Bearer test-access",
        refresh_token="test-refresh",
        device_id="dev-123",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        pin="1234",
    )
    headers = api._get_vehicle_headers(token, vehicle)
    assert headers["Authorization"] == "Bearer test-access"
    assert headers["ccsp-service-id"] == api.CCSP_SERVICE_ID
    assert headers["ccsp-application-id"] == api.APP_ID
    assert headers["ccsp-device-id"] == "dev-123"
    assert headers["country"] == "id"
    assert headers["offset"] == "7"
    assert headers["Ccuccs2protocolsupport"] == "1"


def test_extract_code_from_url_or_input() -> None:
    """Verify OAuth2 authorization code extraction from redirect URLs."""
    redirect_url = (
        "https://apigw-ccs-ap.hyundai.com/api/v1/user/oauth2/redirect"
        "?code=abc123xyz&state=ccsp"
    )
    assert _extract_code_from_url_or_input(redirect_url) == "abc123xyz"
    assert _extract_code_from_url_or_input("code=my_auth_code") == "my_auth_code"
    assert _extract_code_from_url_or_input("plain_password") is None


def test_login_idp_v2_direct_username_password() -> None:
    """Direct username/password login via IDP Connect v2 RSA flow."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")
    rsa_key = RSA.generate(1024)
    jwk = {
        "keys": [
            {
                "kid": "key-id-1",
                "n": _int_to_b64url(rsa_key.n),
                "e": _int_to_b64url(rsa_key.e),
            }
        ]
    }

    def fake_get(url: str, **kwargs: Any) -> MagicMock:
        if "v2/user/oauth2/authorize" in url:
            return _make_mock_response(
                url="https://idpconnect-ap.hyundai.com/auth/account/signin?connector_session_key=sess-99"
            )
        if "v1/accounts/certs" in url:
            return _make_mock_response(json_data=jwk)
        raise AssertionError(f"Unexpected GET {url}")

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("notifications/register"):
            assert kwargs["headers"]["country"] == "id"
            assert kwargs["headers"]["offset"] == "7"
            return _make_mock_response(
                json_data={"retCode": "S", "resMsg": {"deviceId": "dev-id-1"}}
            )
        if url.endswith("/auth/account/signin"):
            form_body = kwargs["data"]
            assert "encryptedPassword=true" in form_body
            assert "kid=key-id-1" in form_body
            assert "connector_session_key=sess-99" in form_body
            # Verify RSA-encrypted password can be decrypted with the private key
            from urllib.parse import parse_qs

            parsed = parse_qs(form_body)
            enc_hex = parsed["password"][0]
            cipher = PKCS1_v1_5.new(rsa_key)
            decrypted = cipher.decrypt(bytes.fromhex(enc_hex), b"sentinel")
            assert decrypted == b"MySecretPass!"
            return _make_mock_response(
                headers={
                    "Location": (
                        "https://apigw-ccs-ap.hyundai.com/api/v1/user/oauth2/redirect"
                        "?code=idp-auth-code-42&state=ccsp"
                    )
                }
            )
        if url.endswith("/auth/api/v2/user/oauth2/token"):
            assert "grant_type=authorization_code" in kwargs["data"]
            assert "code=idp-auth-code-42" in kwargs["data"]
            return _make_mock_response(
                json_data={
                    "access_token": "acc-v2",
                    "refresh_token": "ref-v2",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        raise AssertionError(f"Unexpected POST {url}")

    api.session = MagicMock()
    api.session.cookies.get_dict.return_value = {"XSRF-TOKEN": "csrf-1"}
    api.session.get.side_effect = fake_get
    api.session.post.side_effect = fake_post

    token = api.login("user@id.hyundai.com", "MySecretPass!", pin="1234")
    assert token.access_token == "Bearer acc-v2"
    assert token.refresh_token == "ref-v2"
    assert token.device_id == "dev-id-1"
    assert token.pin == "1234"


def test_login_with_pasted_redirect_url() -> None:
    """Fallback login when user pastes the OAuth2 redirect URL as password."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("notifications/register"):
            return _make_mock_response(
                json_data={"retCode": "S", "resMsg": {"deviceId": "dev-redirect"}}
            )
        if url.endswith("/auth/api/v2/user/oauth2/token"):
            assert "code=redirect-code-777" in kwargs["data"]
            return _make_mock_response(
                json_data={
                    "access_token": "acc-from-url",
                    "refresh_token": "ref-from-url",
                    "token_type": "Bearer",
                    "expires_in": 7200,
                }
            )
        raise AssertionError(f"Unexpected POST {url}")

    api.session = MagicMock()
    api.session.post.side_effect = fake_post

    redirect_url = (
        "https://apigw-ccs-ap.hyundai.com/api/v1/user/oauth2/redirect"
        "?code=redirect-code-777&state=ccsp"
    )
    token = api.login("user@id.hyundai.com", redirect_url, pin="0000")
    assert token.access_token == "Bearer acc-from-url"
    assert token.refresh_token == "ref-from-url"
    assert token.device_id == "dev-redirect"


def test_login_with_pasted_refresh_token() -> None:
    """Fallback login when user pastes a refresh token as password."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")

    def fake_get(url: str, **kwargs: Any) -> MagicMock:
        raise RuntimeError("Direct login unavailable")

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("notifications/register"):
            return _make_mock_response(
                json_data={"retCode": "S", "resMsg": {"deviceId": "dev-rt"}}
            )
        if url.endswith("/auth/api/v2/user/oauth2/token"):
            assert "grant_type=refresh_token" in kwargs["data"]
            assert "refresh_token=my-saved-refresh-token" in kwargs["data"]
            return _make_mock_response(
                json_data={
                    "access_token": "acc-from-rt",
                    "token_type": "Bearer",
                    "expires_in": 3600,
                }
            )
        raise AssertionError(f"Unexpected POST {url}")

    api.session = MagicMock()
    api.session.get.side_effect = fake_get
    api.session.post.side_effect = fake_post

    token = api.login("user@id.hyundai.com", "my-saved-refresh-token")
    assert token.access_token == "Bearer acc-from-rt"
    assert token.refresh_token == "my-saved-refresh-token"


def test_login_failure_raises_authentication_error() -> None:
    """Login raises AuthenticationError when direct login and token exchange fail."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")

    def fake_post(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("notifications/register"):
            return _make_mock_response(
                json_data={"retCode": "S", "resMsg": {"deviceId": "dev-fail"}}
            )
        return _make_mock_response(json_data={"error": "invalid_grant"})

    api.session = MagicMock()
    api.session.get.side_effect = RuntimeError("No authorize")
    api.session.post.side_effect = fake_post

    with pytest.raises(AuthenticationError):
        api.login("user@id.hyundai.com", "wrong-password")


def test_refresh_access_token_and_control_token() -> None:
    """Verify IDP v2 token refresh and PIN control token verification."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")
    token = Token(
        username="user@id.hyundai.com",
        password="pw",
        access_token="Bearer old-access",
        refresh_token="existing-refresh-token",
        device_id="dev-1",
        valid_until=dt.datetime.now(dt.UTC),
        pin="1234",
    )

    api.session = MagicMock()
    api.session.post.return_value = _make_mock_response(
        json_data={
            "access_token": "new-access-token",
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    )

    refreshed = api.refresh_access_token(token)
    assert isinstance(refreshed, Token)
    assert refreshed.access_token == "Bearer new-access-token"
    assert refreshed.refresh_token == "existing-refresh-token"

    api.session.put.return_value = _make_mock_response(
        json_data={"controlToken": "ctrl-tok-99", "expiresTime": 600}
    )
    ctrl_tok, expiry = api._get_control_token(refreshed)
    assert ctrl_tok == "Bearer ctrl-tok-99"
    assert expiry > 0
    put_headers = api.session.put.call_args.kwargs["headers"]
    assert put_headers["country"] == "id"
    assert put_headers["offset"] == "7"
    assert put_headers["ccsp-device-id"] == "dev-1"

    # Error path when PIN is rejected
    bad_token = Token(
        username="user@id.hyundai.com",
        password="pw",
        access_token="Bearer new-access-token",
        refresh_token="existing-refresh-token",
        device_id="dev-1",
        valid_until=dt.datetime.now(dt.UTC),
        pin="0000",
    )
    api.session.put.return_value = _make_mock_response(json_data={})
    with pytest.raises(APIError):
        api._get_control_token(bad_token)


def test_update_vehicle_with_cached_state_ccs2_and_legacy() -> None:
    """Verify both CCS2 (/ccs2/carstatus/latest) and legacy (/status/latest) updates."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")
    token = Token(
        username="user@id.hyundai.com",
        password="pw",
        access_token="Bearer acc",
        refresh_token="ref",
        device_id="dev-1",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        pin="1234",
    )

    # 1. CCS2 vehicle
    ccs2_vehicle = Vehicle(
        id="veh-ccs2",
        name="IONIQ 5",
        model="IONIQ 5",
        registration_date="20240101",
        VIN="KM800000000000001",
        timezone=api.data_timezone,
        engine_type=ENGINE_TYPES.EV,
        ccu_ccs2_protocol_support=1,
    )

    def fake_get_ccs2(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/ccs2/carstatus/latest"):
            return _make_mock_response(
                json_data={
                    "retCode": "S",
                    "resMsg": {
                        "state": {
                            "Vehicle": {
                                "Drivetrain": {
                                    "Odometer": 12345.0,
                                    "FuelSystem": {"DTE": {"Total": 350.0, "Unit": 1}},
                                },
                                "Green": {
                                    "BatteryManagement": {
                                        "BatteryRemain": {"Ratio": 82.0}
                                    }
                                },
                            }
                        }
                    },
                }
            )
        if url.endswith("/location/park"):
            return _make_mock_response(
                json_data={
                    "retCode": "S",
                    "resMsg": {
                        "coord": {"lat": -6.2088, "lon": 106.8456},
                        "time": "20260418120000",
                    },
                }
            )
        raise AssertionError(f"Unexpected GET {url}")

    api.session = MagicMock()
    api.session.get.side_effect = fake_get_ccs2
    api.session.post.return_value = _make_mock_response(
        json_data={"retCode": "S", "resMsg": {}}
    )

    api.update_vehicle_with_cached_state(token, ccs2_vehicle)
    assert ccs2_vehicle.ev_battery_percentage == 82.0
    assert ccs2_vehicle.location_latitude == -6.2088
    assert ccs2_vehicle.location_longitude == 106.8456

    # 2. Legacy vehicle with vehicleStatusInfo wrapper
    legacy_vehicle = Vehicle(
        id="veh-legacy",
        name="CRETA",
        model="CRETA",
        registration_date="20230101",
        VIN="MHY00000000000002",
        timezone=api.data_timezone,
        engine_type=ENGINE_TYPES.ICE,
        ccu_ccs2_protocol_support=0,
    )

    def fake_get_legacy(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/status/latest"):
            return _make_mock_response(
                json_data={
                    "retCode": "S",
                    "resMsg": {
                        "vehicleStatusInfo": {
                            "vehicleStatus": {
                                "time": "20260418120000",
                                "engine": False,
                                "doorLock": True,
                                "battery": {"batSoc": 90},
                            },
                            "odometer": {"value": 5432, "unit": 1},
                            "vehicleLocation": {
                                "coord": {"lat": -6.1751, "lon": 106.8650},
                                "time": "20260418120000",
                            },
                        }
                    },
                }
            )
        raise AssertionError(f"Unexpected GET {url}")

    api.session.get.side_effect = fake_get_legacy
    api.update_vehicle_with_cached_state(token, legacy_vehicle)
    assert legacy_vehicle.car_battery_percentage == 90
    assert legacy_vehicle.is_locked is True
    assert legacy_vehicle.odometer == 5432.0
    assert legacy_vehicle.odometer_unit == "km"


def test_remote_controls_ccs2_and_legacy() -> None:
    """Verify lock, climate, and charge port commands on HyundaiBlueLinkApiID."""
    api = HyundaiBlueLinkApiID(region=REGION_INDONESIA_ID, brand=2, language="en")
    token = Token(
        username="user@id.hyundai.com",
        password="pw",
        access_token="Bearer acc",
        refresh_token="ref",
        device_id="dev-1",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        pin="1234",
        control_token="Bearer ctrl-cached",
        control_token_expiry=dt.datetime.now().timestamp() + 600,
    )
    ccs2_vehicle = Vehicle(
        id="veh-ccs2",
        name="IONIQ 5",
        model="IONIQ 5",
        registration_date="20240101",
        VIN="KM800000000000001",
        timezone=api.data_timezone,
        engine_type=ENGINE_TYPES.EV,
        ccu_ccs2_protocol_support=1,
    )

    api.session = MagicMock()
    api.session.post.return_value = _make_mock_response(
        json_data={"retCode": "S", "msgId": "msg-1"}
    )

    msg_id = api.lock_action(token, ccs2_vehicle, VEHICLE_LOCK_ACTION.LOCK)
    assert msg_id == "msg-1"
    assert api.session.post.call_args.args[0].endswith(
        "/api/v2/spa/vehicles/veh-ccs2/ccs2/control/door"
    )

    msg_id = api.charge_port_action(token, ccs2_vehicle, CHARGE_PORT_ACTION.OPEN)
    assert msg_id == "msg-1"
    assert api.session.post.call_args.args[0].endswith(
        "/api/v2/spa/vehicles/veh-ccs2/ccs2/control/portdoor"
    )
    assert api.session.post.call_args.kwargs["json"] == {"command": "open"}

    # Legacy climate setpoint below 17.0°C is clamped to 17.0°C ("00H")
    legacy_vehicle = Vehicle(
        id="veh-legacy",
        name="CRETA",
        model="CRETA",
        registration_date="20230101",
        VIN="MHY00000000000002",
        timezone=api.data_timezone,
        engine_type=ENGINE_TYPES.ICE,
        ccu_ccs2_protocol_support=0,
    )
    opts = ClimateRequestOptions(
        set_temp=15.0, duration=10, defrost=False, climate=True
    )
    msg_id = api.start_climate(token, legacy_vehicle, opts)
    assert msg_id == "msg-1"
    assert opts.set_temp == 17.0


def test_constructor_defaults_and_kwargs() -> None:
    """HyundaiBlueLinkApiID can be initialized with or without positional/keyword args."""
    api_default = HyundaiBlueLinkApiID()
    assert api_default.BASE_URL == "apigw-ccs-ap.hyundai.com"
    assert api_default.LANGUAGE == "en"

    api_pos = HyundaiBlueLinkApiID(10, 1, "id")
    assert api_pos.LANGUAGE == "id"

    api_kwargs = HyundaiBlueLinkApiID(
        region=10, brand=2, language="en", extra_arg="ignored"
    )
    assert api_kwargs.BASE_URL == "apigw-ccs-ap.hyundai.com"

    # Verify get_vehicles and _vehicle_from_spa_entry handle Indonesian UserVehicle
    # payloads where "vin" is omitted without raising KeyError.
    api_default.session = MagicMock()
    api_default.session.get.return_value = _make_mock_response(
        json_data={
            "retCode": "S",
            "resMsg": {
                "vehicles": [
                    {
                        "vehicleId": "v-id-123",
                        "vehicleName": "IONIQ 5",
                        "nickname": "My IONIQ 5",
                        "type": "EV",
                        "year": "2024",
                        "regDate": "20240501",
                        "ccuCCS2ProtocolSupport": 1,
                    }
                ]
            },
        }
    )
    token = Token(
        username="user@id.hyundai.com",
        password="pw",
        access_token="Bearer acc",
        refresh_token="ref",
        device_id="dev-1",
        valid_until=dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        pin="1234",
    )
    vehicles = api_default.get_vehicles(token)
    assert len(vehicles) == 1
    assert vehicles[0].id == "v-id-123"
    assert vehicles[0].name == "My IONIQ 5"
    assert vehicles[0].model == "IONIQ 5"
    assert vehicles[0].VIN == "v-id-123"
    assert vehicles[0].year == 2024
    assert vehicles[0].engine_type == ENGINE_TYPES.EV
    assert vehicles[0].ccu_ccs2_protocol_support == 1

    # Verify CCS2 update when DTE.Total is None and Location.TimeStamp has Year=0
    api_default._update_vehicle_properties_ccs2(
        vehicles[0],
        {
            "Drivetrain": {"Odometer": 1000.0, "FuelSystem": {"DTE": {"Total": None}}},
            "Location": {"TimeStamp": {"Year": 0, "Month": 0, "Day": 0}},
        },
    )
    assert vehicles[0].odometer == 1000.0
    assert vehicles[0].total_driving_range is None


def test_manifest_requirements_and_validate_input() -> None:
    """Verify manifest.json has pycryptodome>=3.19.0 and validate_input works cleanly."""
    import json
    from pathlib import Path

    from homeassistant.const import CONF_REGION

    from custom_components.kia_uvo.config_flow import validate_input
    from custom_components.kia_uvo.const import CONF_BRAND

    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "custom_components"
        / "kia_uvo"
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert "pycryptodome>=3.19.0" in manifest["requirements"]

    hass = MagicMock()
    hass.config.language = "en"

    async def _run_sync(func: Any, *args: Any) -> Any:
        return func(*args)

    hass.async_add_executor_job = _run_sync

    vm = VehicleManager(
        region=REGION_INDONESIA_ID,
        brand=2,
        username="user@id.hyundai.com",
        password="pw",
        pin="1234",
        language="en",
    )
    assert isinstance(vm.api, HyundaiBlueLinkApiID)
    vm.login = MagicMock(return_value=True)

    coro = validate_input(
        hass,
        {CONF_REGION: REGION_INDONESIA_ID, CONF_BRAND: 2},
        vm,
    )
    try:
        coro.send(None)
    except StopIteration as stop:
        result = stop.value
    else:
        raise AssertionError("Coroutine did not complete synchronously")

    assert result is True
