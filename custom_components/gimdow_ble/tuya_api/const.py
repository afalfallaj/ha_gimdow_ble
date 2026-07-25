"""Constants and enums for the Tuya OpenAPI client."""
from dataclasses import dataclass
from enum import Enum

TUYA_ERROR_CODE_TOKEN_INVALID = 1010

TO_C_CUSTOM_REFRESH_TOKEN_API = "/v1.0/iot-03/users/token/"
TO_C_SMART_HOME_REFRESH_TOKEN_API = "/v1.0/token/"

TO_C_CUSTOM_TOKEN_API = "/v1.0/iot-03/users/login"
TO_C_SMART_HOME_TOKEN_API = "/v1.0/iot-01/associated-users/actions/authorized-login"

VERSION = "0.6.6"


class AuthType(Enum):
    """Tuya Cloud Auth Type."""
    SMART_HOME = 0
    CUSTOM = 1


class TuyaCloudOpenAPIEndpoint:
    """Tuya Cloud Open API Endpoint."""
    CHINA = "https://openapi.tuyacn.com"
    AMERICA = "https://openapi.tuyaus.com"
    AMERICA_AZURE = "https://openapi-ueaz.tuyaus.com"
    EUROPE = "https://openapi.tuyaeu.com"
    EUROPE_MS = "https://openapi-weaz.tuyaeu.com"
    INDIA = "https://openapi.tuyain.com"


@dataclass
class TuyaRegion:
    """Describe a supported Tuya cloud region."""
    name: str
    country_code: str
    endpoint: str


TUYA_REGIONS = [
    TuyaRegion("America", "1", TuyaCloudOpenAPIEndpoint.AMERICA),
    TuyaRegion("Europe", "44", TuyaCloudOpenAPIEndpoint.EUROPE),
    TuyaRegion("China", "86", TuyaCloudOpenAPIEndpoint.CHINA),
    TuyaRegion("India", "91", TuyaCloudOpenAPIEndpoint.INDIA),
]
