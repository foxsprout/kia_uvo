"""Hyundai Bluelink Indonesia (AP) API implementation."""

from __future__ import annotations

import base64
from collections.abc import Callable
import contextlib
import datetime as dt
import logging
import math
import random
import time
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlparse
import uuid
from zoneinfo import ZoneInfo

from Crypto.Cipher import PKCS1_v1_5
from Crypto.PublicKey import RSA
from hyundai_kia_connect_api import VehicleManager
from hyundai_kia_connect_api.ApiImpl import ClimateRequestOptions, OTPRequest
from hyundai_kia_connect_api.ApiImplType1 import (
    USER_AGENT_OK_HTTP,
    _check_response_for_errors,
)
import hyundai_kia_connect_api.const as api_const
from hyundai_kia_connect_api.const import (
    BRAND_HYUNDAI,
    BRANDS,
    CHARGE_PORT_ACTION,
    DOMAIN,
    ENGINE_TYPES,
    ENGINE_TYPES_BY_VEHICLE_TYPE,
    SEAT_LOCATION,
)
from hyundai_kia_connect_api.exceptions import APIError, AuthenticationError
from hyundai_kia_connect_api.KiaUvoApiAU import KiaUvoApiAU
from hyundai_kia_connect_api.Token import Token
from hyundai_kia_connect_api.utils import get_child_value, parse_datetime
from hyundai_kia_connect_api.Vehicle import Vehicle

_LOGGER = logging.getLogger(__name__)

REGION_INDONESIA: str = "Indonesia"
REGION_INDONESIA_ID: int = 10


def _b64url_to_int(val: str) -> int:
    padding = "=" * ((4 - len(val) % 4) % 4)
    raw_bytes = base64.urlsafe_b64decode(val + padding)
    return int.from_bytes(raw_bytes, byteorder="big")


def _extract_code_from_url_or_input(value: str) -> str | None:
    """Extract an OAuth2 authorization code from a redirect URL or query string."""
    stripped = value.strip()
    if "code=" not in stripped:
        return None
    if "://" in stripped or stripped.startswith("?"):
        parsed = urlparse(stripped)
        query = parse_qs(parsed.query or stripped.lstrip("?"))
        code_list = query.get("code")
        if code_list and code_list[0]:
            return code_list[0]
    if stripped.startswith("code="):
        query = parse_qs(stripped)
        code_list = query.get("code")
        if code_list and code_list[0]:
            return code_list[0]
    return None


class HyundaiBlueLinkApiID(KiaUvoApiAU):
    """Hyundai Bluelink API client for Indonesia (com.hyundai.bluelink.ap)."""

    data_timezone = ZoneInfo("Asia/Jakarta")
    temperature_range = tuple(x * 0.5 for x in range(34, 64))

    BASE_URL: str = "apigw-ccs-ap.hyundai.com"
    USER_API_URL: str = "https://" + BASE_URL + "/api/v1/user/"
    SPA_API_URL: str = "https://" + BASE_URL + "/api/v1/spa/"
    SPA_API_URL_V2: str = "https://" + BASE_URL + "/api/v2/spa/"

    IDP_URL: str = "https://idpconnect-ap.hyundai.com"
    IDP_AUTH_API_URL: str = IDP_URL + "/auth/api/"
    REDIRECT_URI: str = USER_API_URL + "oauth2/redirect"

    CLIENT_ID: str = "a3f38b3c-5948-48e9-97ee-1176c2662292"
    CCSP_SERVICE_ID: str = CLIENT_ID
    APP_ID: str = "4250ad16-2a86-4cd4-8e33-7a6f4f265f94"
    CLIENT_SECRET: str = "2IHLauPjGvFa7xa6t3FWuBorlZAUfNBTdD1RlB410DVF307d"
    BASIC_AUTHORIZATION: str = (
        "Basic YTNmMzhiM2MtNTk0OC00OGU5LTk3ZWUtMTE3NmMyNjYyMjky"
        "OjJJSExhdVBqR3ZGYTd4YTZ0M0ZXdUJvcmxaQVVmTkJUZEQxUmxCNDEwRFZGMzA3ZA=="
    )
    CFB: bytes = base64.b64decode(
        "RFtoRq/vDXJmRndoZaZQyfOot7OrIqGVFj96iY2WL3yyH5Z/pUvlUhqmCxD2t+D65SQ="
    )
    COUNTRY_CODE: str = "id"
    OFFSET: str = "7"
    PUSH_TYPE: str = "GCM"

    def __init__(
        self,
        region: int = 10,
        brand: int = 1,
        language: str = "en",
        **kwargs: Any,
    ) -> None:
        """Initialize the Indonesia API client."""
        api_const.REGIONS.setdefault(region, REGION_INDONESIA)
        super().__init__(region=region, brand=brand, language=language)
        self.LANGUAGE = language
        self.BASE_URL = "apigw-ccs-ap.hyundai.com"
        self.USER_API_URL = "https://" + self.BASE_URL + "/api/v1/user/"
        self.SPA_API_URL = "https://" + self.BASE_URL + "/api/v1/spa/"
        self.SPA_API_URL_V2 = "https://" + self.BASE_URL + "/api/v2/spa/"
        self.REDIRECT_URI = self.USER_API_URL + "oauth2/redirect"
        self.CLIENT_ID = "a3f38b3c-5948-48e9-97ee-1176c2662292"
        self.CCSP_SERVICE_ID = self.CLIENT_ID
        self.APP_ID = "4250ad16-2a86-4cd4-8e33-7a6f4f265f94"
        self.BASIC_AUTHORIZATION = (
            "Basic YTNmMzhiM2MtNTk0OC00OGU5LTk3ZWUtMTE3NmMyNjYyMjky"
            "OjJJSExhdVBqR3ZGYTd4YTZ0M0ZXdUJvcmxaQVVmTkJUZEQxUmxCNDEwRFZGMzA3ZA=="
        )
        self.CFB = base64.b64decode(
            "RFtoRq/vDXJmRndoZaZQyfOot7OrIqGVFj96iY2WL3yyH5Z/pUvlUhqmCxD2t+D65SQ="
        )
        self.PUSH_TYPE = "GCM"

    def _get_drv_seat_loc(self, vehicle: Vehicle) -> SEAT_LOCATION:
        return SEAT_LOCATION.RIGHT

    def _get_authenticated_headers(
        self, token: Token, ccs2_support: int | None = None
    ) -> dict[str, Any]:
        headers = super()._get_authenticated_headers(token, ccs2_support)
        headers["offset"] = self.OFFSET
        headers["country"] = self.COUNTRY_CODE
        return headers

    def _get_vehicle_headers(self, token: Token, vehicle: Vehicle) -> dict[str, Any]:
        return self._get_authenticated_headers(token, vehicle.ccu_ccs2_protocol_support)

    def _vehicle_from_spa_entry(self, entry: dict[str, Any]) -> Vehicle:
        vehicle_type = entry.get("type") or entry.get("evType") or entry.get("fuelType")
        vehicle_id = str(entry.get("vehicleId") or entry.get("id") or "")
        vehicle_name = str(
            entry.get("vehicleName")
            or entry.get("modelName")
            or entry.get("carName")
            or entry.get("nickname")
            or vehicle_id
        )
        nickname = str(entry.get("nickname") or vehicle_name)
        return Vehicle(
            id=vehicle_id,
            name=nickname,
            model=vehicle_name,
            registration_date=str(
                entry.get("regDate") or entry.get("enrollDate") or ""
            ),
            VIN=str(entry.get("vin") or ""),
            timezone=self.data_timezone,
            engine_type=ENGINE_TYPES_BY_VEHICLE_TYPE.get(
                str(vehicle_type) if vehicle_type is not None else ""
            ),
            ccu_ccs2_protocol_support=int(entry.get("ccuCCS2ProtocolSupport") or 0),
        )

    def _get_device_id(self, stamp: str) -> str:
        my_hex = f"{random.randrange(10**80):064x}"
        registration_id = my_hex[:64]
        url = self.SPA_API_URL + "notifications/register"
        payload = {
            "pushRegId": registration_id,
            "pushType": self.PUSH_TYPE,
            "uuid": str(uuid.uuid4()),
        }
        headers = {
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "ccsp-application-id": self.APP_ID,
            "Stamp": stamp,
            "offset": self.OFFSET,
            "country": self.COUNTRY_CODE,
            "Content-Type": "application/json;charset=UTF-8",
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        _LOGGER.debug(f"{DOMAIN} - Get Device ID request: {url} {headers} {payload}")
        response = self.session.post(url, headers=headers, json=payload).json()
        _check_response_for_errors(response)
        _LOGGER.debug(f"{DOMAIN} - Get Device ID response: {response}")
        return cast(str, response["resMsg"]["deviceId"])

    def _get_control_token(self, token: Token) -> tuple[str, float]:
        if token.control_token is not None and token.control_token_expiry > time.time():
            return token.control_token, token.control_token_expiry

        url = self.USER_API_URL + "pin?token="
        headers = self._get_authenticated_headers(token)
        headers["Content-type"] = "application/json"
        data = {"deviceId": token.device_id, "pin": token.pin}
        response = self.session.put(url, json=data, headers=headers).json()
        if response.get("controlToken") is None:
            _LOGGER.debug(f"{DOMAIN} - Get Control Token Response {response}")
            raise APIError("PIN verification failed, ensure PIN is entered correctly.")
        control_token = "Bearer " + response["controlToken"]
        control_token_expire_at = math.floor(
            dt.datetime.now().timestamp() + response["expiresTime"]
        )
        token.control_token = control_token
        token.control_token_expiry = control_token_expire_at
        return control_token, control_token_expire_at

    def _parse_token_response(
        self, response_json: dict[str, Any], fallback_refresh_token: str = ""
    ) -> tuple[str, str, int]:
        if "access_token" not in response_json:
            _check_response_for_errors(response_json)
            raise AuthenticationError(
                f"Token response missing access_token: {response_json}"
            )
        token_type = response_json.get("token_type", "Bearer")
        access_token = f"{token_type} {response_json['access_token']}"
        refresh_token = response_json.get("refresh_token") or fallback_refresh_token
        expires_in = int(response_json.get("expires_in", 3600))
        return access_token, refresh_token, expires_in

    def _exchange_authorization_code(self, code: str) -> tuple[str, str, int]:
        """Exchange an OAuth2 authorization code via IDP v2 or CCSP v1."""
        idp_token_url = self.IDP_AUTH_API_URL + "v2/user/oauth2/token"
        idp_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        idp_data = urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.REDIRECT_URI,
                "client_id": self.CLIENT_ID,
                "client_secret": self.CLIENT_SECRET,
            }
        )
        try:
            response = self.session.post(
                idp_token_url, data=idp_data, headers=idp_headers
            ).json()
            if "access_token" in response:
                return self._parse_token_response(response)
            _LOGGER.debug(f"{DOMAIN} - IDP v2 code exchange response: {response}")
        except Exception as err:
            _LOGGER.debug(f"{DOMAIN} - IDP v2 code exchange failed: {err}")

        v1_token_url = self.USER_API_URL + "oauth2/token"
        v1_headers = {
            "Authorization": self.BASIC_AUTHORIZATION,
            "Stamp": self._get_stamp(),
            "Content-type": "application/x-www-form-urlencoded",
            "Host": self.BASE_URL,
            "offset": self.OFFSET,
            "country": self.COUNTRY_CODE,
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        v1_data = f"grant_type=authorization_code&redirect_uri={self.REDIRECT_URI}&code={code}"
        response = self.session.post(
            v1_token_url, data=v1_data, headers=v1_headers
        ).json()
        return self._parse_token_response(response)

    def _exchange_refresh_token(self, refresh_token: str) -> tuple[str, str, int]:
        """Exchange a refresh token via IDP v2 or CCSP v1."""
        idp_token_url = self.IDP_AUTH_API_URL + "v2/user/oauth2/token"
        idp_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        idp_data = urlencode(
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": self.CLIENT_ID,
                "client_secret": self.CLIENT_SECRET,
            }
        )
        try:
            response = self.session.post(
                idp_token_url, data=idp_data, headers=idp_headers
            ).json()
            if "access_token" in response:
                return self._parse_token_response(response, refresh_token)
            _LOGGER.debug(f"{DOMAIN} - IDP v2 refresh token response: {response}")
        except Exception as err:
            _LOGGER.debug(f"{DOMAIN} - IDP v2 refresh token failed: {err}")

        v1_token_url = self.USER_API_URL + "oauth2/token"
        v1_headers = {
            "Authorization": self.BASIC_AUTHORIZATION,
            "Stamp": self._get_stamp(),
            "Content-type": "application/x-www-form-urlencoded",
            "Host": self.BASE_URL,
            "offset": self.OFFSET,
            "country": self.COUNTRY_CODE,
            "Connection": "close",
            "Accept-Encoding": "gzip, deflate",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        v1_data = (
            "grant_type=refresh_token"
            "&redirect_uri=https://www.getpostman.com/oauth2/callback"
            f"&refresh_token={refresh_token}"
        )
        response = self.session.post(
            v1_token_url, data=v1_data, headers=v1_headers
        ).json()
        return self._parse_token_response(response, refresh_token)

    def _encrypt_password_rsa(self, password: str) -> tuple[str, str]:
        """Fetch IDP JWK certificate and RSA PKCS1_v1_5 encrypt password."""
        certs_url = self.IDP_AUTH_API_URL + "v1/accounts/certs"
        response = self.session.get(certs_url).json()
        key_data = response["keys"][0]
        kid = cast(str, key_data["kid"])
        n = _b64url_to_int(key_data["n"])
        e = _b64url_to_int(key_data["e"])
        rsa_key = RSA.construct((n, e))
        cipher = PKCS1_v1_5.new(rsa_key)
        encrypted_bytes = cipher.encrypt(password.encode("utf-8"))
        return kid, encrypted_bytes.hex()

    def _login_idp_v2(self, username: str, password: str) -> str:
        """Perform IDP Connect v2 username/password sign-in and return auth code."""
        authorize_url = (
            f"{self.IDP_AUTH_API_URL}v2/user/oauth2/authorize"
            f"?response_type=code&client_id={self.CLIENT_ID}"
            f"&redirect_uri={self.REDIRECT_URI}&lang=id&state=ccsp"
            f"&ui_locales=id-ID&country={self.COUNTRY_CODE}"
        )
        auth_resp = self.session.get(authorize_url)
        connector_session_key = ""
        if getattr(auth_resp, "url", None):
            parsed_auth = urlparse(str(auth_resp.url))
            qs = parse_qs(parsed_auth.query)
            connector_session_key = qs.get("connector_session_key", [""])[0]

        kid, encrypted_password = self._encrypt_password_rsa(password)
        cookies = (
            self.session.cookies.get_dict() if hasattr(self.session, "cookies") else {}
        )
        csrf_token = cookies.get("XSRF-TOKEN", "")

        signin_url = self.IDP_URL + "/auth/account/signin"
        signin_headers = {
            "Content-Type": "application/x-www-form-urlencoded",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        signin_data = urlencode(
            {
                "client_id": self.CLIENT_ID,
                "encryptedPassword": "true",
                "password": encrypted_password,
                "redirect_uri": self.REDIRECT_URI,
                "scope": "",
                "nonce": "",
                "state": "ccsp",
                "username": username,
                "connector_session_key": connector_session_key,
                "kid": kid,
                "_csrf": csrf_token,
            }
        )
        signin_resp = self.session.post(
            signin_url,
            data=signin_data,
            headers=signin_headers,
            allow_redirects=False,
        )
        location = signin_resp.headers.get("Location", "")
        code = _extract_code_from_url_or_input(location)
        if code:
            return code
        if getattr(signin_resp, "url", None):
            code = _extract_code_from_url_or_input(str(signin_resp.url))
            if code:
                return code
        with contextlib.suppress(Exception):
            body = signin_resp.json()
            redirect_url = body.get("redirectUrl", "")
            code = _extract_code_from_url_or_input(redirect_url)
            if code:
                return code
        raise AuthenticationError(
            "IDP Connect v2 sign-in did not return an authorization code"
        )

    def _login_ccsp_v1(self, username: str, password: str) -> str:
        """Fallback CCSP v1 sign-in returning an authorization code."""
        url = (
            f"{self.USER_API_URL}oauth2/authorize"
            f"?response_type=code&state=ccsp&client_id={self.CCSP_SERVICE_ID}"
            f"&redirect_uri={self.REDIRECT_URI}&lang={self.LANGUAGE}"
        )
        headers = {
            "ccsp-application-id": self.APP_ID,
            "ccsp-service-id": self.CCSP_SERVICE_ID,
            "Stamp": self._get_stamp(),
            "offset": self.OFFSET,
            "country": self.COUNTRY_CODE,
            "Host": self.BASE_URL,
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "User-Agent": USER_AGENT_OK_HTTP,
        }
        response = self.session.get(url, headers=headers)
        cookies = response.cookies.get_dict()
        self._set_session_language(cookies)
        return self._get_authorization_code_with_redirect_url(
            username, password, cookies
        )

    def login(
        self,
        username: str,
        password: str,
        pin: str | None = None,
        **kwargs: Any,
    ) -> Token:
        """Log in to Hyundai Bluelink Indonesia.

        Supports:
        1. Pasting an OAuth2 redirect URL or ``code=...`` query string in ``password``.
        2. Direct username/password login (IDP Connect v2 with CCSP v1 fallback).
        3. Pasting a refresh token in ``password`` as fallback.
        """
        stamp = self._get_stamp()
        device_id = self._get_device_id(stamp)

        code = _extract_code_from_url_or_input(password)
        if code is not None:
            access_token, refresh_token, expires_in = self._exchange_authorization_code(
                code
            )
        else:
            auth_code: str | None = None
            try:
                auth_code = self._login_idp_v2(username, password)
            except Exception as idp_err:
                _LOGGER.debug(
                    f"{DOMAIN} - IDP v2 direct login failed ({idp_err}), "
                    "trying CCSP v1 signin"
                )
                try:
                    auth_code = self._login_ccsp_v1(username, password)
                except Exception as v1_err:
                    _LOGGER.debug(
                        f"{DOMAIN} - CCSP v1 direct login failed ({v1_err}), "
                        "trying password as refresh token"
                    )

            if auth_code is not None:
                access_token, refresh_token, expires_in = (
                    self._exchange_authorization_code(auth_code)
                )
            else:
                try:
                    access_token, refresh_token, expires_in = (
                        self._exchange_refresh_token(password.strip())
                    )
                except Exception as refresh_err:
                    raise AuthenticationError(
                        "Login failed: invalid username/password, redirect URL, "
                        "or refresh token."
                    ) from refresh_err

        valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in)
        return Token(
            username=username,
            password=password,
            access_token=access_token,
            refresh_token=refresh_token,
            device_id=device_id,
            valid_until=valid_until,
            pin=pin,
        )

    def refresh_access_token(self, token: Token) -> Token | OTPRequest:
        """Refresh access token using IDP Connect v2 or CCSP v1."""
        if token.refresh_token:
            try:
                access_token, new_refresh_token, expires_in = (
                    self._exchange_refresh_token(token.refresh_token)
                )
                valid_until = dt.datetime.now(dt.UTC) + dt.timedelta(seconds=expires_in)
                return Token(
                    username=token.username,
                    password=token.password,
                    access_token=access_token,
                    refresh_token=new_refresh_token,
                    device_id=token.device_id,
                    valid_until=valid_until,
                    pin=token.pin,
                )
            except Exception:
                _LOGGER.warning(
                    f"{DOMAIN} - Refresh token exchange failed, "
                    "falling back to full login"
                )
        return self.login(token.username, token.password, token.pin)

    def update_vehicle_with_cached_state(self, token: Token, vehicle: Vehicle) -> None:
        """Fetch latest cached vehicle status (CCS2 or legacy)."""
        url = self.SPA_API_URL + "vehicles/" + vehicle.id
        is_ccs2 = vehicle.ccu_ccs2_protocol_support != 0
        if is_ccs2:
            url += "/ccs2/carstatus/latest"
        else:
            url += "/status/latest"

        response = self.session.get(
            url,
            headers=self._get_authenticated_headers(
                token, vehicle.ccu_ccs2_protocol_support
            ),
        ).json()
        _LOGGER.debug(f"{DOMAIN} - get_cached_vehicle_status response: {response}")
        _check_response_for_errors(response)

        if is_ccs2:
            state = response["resMsg"]["state"]["Vehicle"]
            self._update_vehicle_properties_ccs2(vehicle, state)
            location = self._get_location(token, vehicle)
            if location and get_child_value(location, "coord.lat"):
                vehicle.location = (
                    get_child_value(location, "coord.lat"),
                    get_child_value(location, "coord.lon"),
                    parse_datetime(
                        get_child_value(location, "time"), self.data_timezone
                    ),
                )
        else:
            res_msg = response["resMsg"]
            if isinstance(res_msg, dict) and "vehicleStatusInfo" in res_msg:
                info = res_msg["vehicleStatusInfo"]
                status = dict(info.get("vehicleStatus", {}))
                if "odometer" in info and "odometer" not in status:
                    status["odometer"] = info["odometer"]
                embedded_location = info.get("vehicleLocation")
            else:
                status = res_msg
                embedded_location = None
            location = embedded_location or self._get_location(token, vehicle)
            self._update_vehicle_properties(
                vehicle,
                {
                    "status": status,
                    "vehicleLocation": location,
                },
            )

        if (
            vehicle.engine_type == ENGINE_TYPES.EV
            or vehicle.engine_type == ENGINE_TYPES.PHEV
        ):
            try:
                drive_state = self._get_driving_info(token, vehicle)
            except Exception as err:
                _LOGGER.debug(f"{DOMAIN} - Failed to parse driving info: {err}")
            else:
                self._update_vehicle_drive_info(vehicle, drive_state)

    def start_climate(
        self, token: Token, vehicle: Vehicle, options: ClimateRequestOptions
    ) -> str:
        """Start climate control, clamping legacy setpoint to supported range."""
        if (
            not vehicle.ccu_ccs2_protocol_support
            and options.set_temp is not None
            and self.temperature_range
        ):
            min_t = self.temperature_range[0]
            max_t = self.temperature_range[-1]
            options.set_temp = max(min_t, min(max_t, float(options.set_temp)))
        return super().start_climate(token, vehicle, options)

    def charge_port_action(
        self, token: Token, vehicle: Vehicle, action: CHARGE_PORT_ACTION
    ) -> str:
        """Open or close the charge port door (CCS2 or legacy)."""
        if vehicle.ccu_ccs2_protocol_support:
            url = (
                self.SPA_API_URL_V2
                + "vehicles/"
                + vehicle.id
                + "/ccs2/control/portdoor"
            )
            payload: dict[str, Any] = {"command": action.value}
            _LOGGER.debug(f"{DOMAIN} - Charge Port Action Request: {payload}")
            response = self.session.post(
                url, json=payload, headers=self._get_control_headers(token, vehicle)
            ).json()
            _LOGGER.debug(f"{DOMAIN} - Charge Port Action Response: {response}")
            _check_response_for_errors(response)
            return cast(str, response["msgId"])
        return super().charge_port_action(token, vehicle, action)


_ORIGINAL_GET_IMPLEMENTATION: Callable[..., Any] | None = None


def register_indonesia_region() -> None:
    """Register Indonesia region in hyundai_kia_connect_api."""
    global _ORIGINAL_GET_IMPLEMENTATION
    api_const.REGIONS[REGION_INDONESIA_ID] = REGION_INDONESIA

    if _ORIGINAL_GET_IMPLEMENTATION is not None:
        return

    _ORIGINAL_GET_IMPLEMENTATION = VehicleManager.get_implementation_by_region_brand

    def _get_implementation_by_region_brand(
        region: int = REGION_INDONESIA_ID,
        brand: int = 1,
        language: str = "en",
        **kwargs: Any,
    ) -> Any:
        if (
            int(region) == REGION_INDONESIA_ID
            or api_const.REGIONS.get(region) == REGION_INDONESIA
        ):
            if BRANDS.get(brand) == BRAND_HYUNDAI:
                return HyundaiBlueLinkApiID(
                    region=int(region), brand=int(brand), language=language, **kwargs
                )
            return HyundaiBlueLinkApiID(
                region=int(region), brand=int(brand), language=language, **kwargs
            )
        assert _ORIGINAL_GET_IMPLEMENTATION is not None
        return _ORIGINAL_GET_IMPLEMENTATION(region, brand, language)

    VehicleManager.get_implementation_by_region_brand = staticmethod(  # type: ignore[method-assign]
        _get_implementation_by_region_brand
    )


register_indonesia_region()
