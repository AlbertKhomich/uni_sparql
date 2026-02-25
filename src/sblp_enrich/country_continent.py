from typing import Dict, Optional, Set

# Derived from ISO country -> continent assignments (country-and-continent-codes-list-csv).
_CONTINENT_NAME_BY_CODE: Dict[str, str] = {
    "AF": "Africa",
    "AS": "Asia",
    "EU": "Europe",
    "NA": "North America",
    "OC": "Oceania",
    "SA": "South America",
    "AN": "Antarctica",
}

_COUNTRY_CODES_BY_CONTINENT: Dict[str, Set[str]] = {
    "AF": {
        "AO", "BF", "BI", "BJ", "BW", "CD", "CF", "CG", "CI", "CM", "CV", "DJ", "DZ", "EG",
        "EH", "ER", "ET", "GA", "GH", "GM", "GN", "GQ", "GW", "KE", "KM", "LR", "LS", "LY",
        "MA", "MG", "ML", "MR", "MU", "MW", "MZ", "NA", "NE", "NG", "RE", "RW", "SC", "SD",
        "SH", "SL", "SN", "SO", "SS", "ST", "SZ", "TD", "TG", "TN", "TZ", "UG", "YT", "ZA",
        "ZM", "ZW",
    },
    "AS": {
        "AE", "AF", "AM", "AZ", "BD", "BH", "BN", "BT", "CC", "CN", "CX", "CY", "GE", "HK",
        "ID", "IL", "IN", "IO", "IQ", "IR", "JO", "JP", "KG", "KH", "KP", "KR", "KW", "KZ",
        "LA", "LB", "LK", "MM", "MN", "MO", "MV", "MY", "NP", "OM", "PH", "PK", "PS", "QA",
        "SA", "SG", "SY", "TH", "TJ", "TL", "TM", "TR", "TW", "UZ", "VN", "YE",
    },
    "EU": {
        "AD", "AL", "AT", "AX", "BA", "BE", "BG", "BY", "CH", "CZ", "DE", "DK", "EE", "ES",
        "FI", "FO", "FR", "GB", "GG", "GI", "GR", "HR", "HU", "IE", "IM", "IS", "IT", "JE",
        "LI", "LT", "LU", "LV", "MC", "MD", "ME", "MK", "MT", "NL", "NO", "PL", "PT", "RO",
        "RS", "RU", "SE", "SI", "SJ", "SK", "SM", "UA", "VA", "XK",
    },
    "NA": {
        "AG", "AI", "AN", "AW", "BB", "BL", "BM", "BQ", "BS", "BZ", "CA", "CR", "CU", "CW",
        "DM", "DO", "GD", "GL", "GP", "GT", "HN", "HT", "JM", "KN", "KY", "LC", "MF", "MQ",
        "MS", "MX", "NI", "PA", "PM", "PR", "SV", "SX", "TC", "TT", "US", "VC", "VG", "VI",
    },
    "OC": {
        "AS", "AU", "CK", "FJ", "FM", "GU", "KI", "MH", "MP", "NC", "NF", "NR", "NU", "NZ",
        "PF", "PG", "PN", "PW", "SB", "TK", "TO", "TV", "VU", "WF", "WS",
    },
    "SA": {
        "AR", "BO", "BR", "CL", "CO", "EC", "FK", "GF", "GY", "PE", "PY", "SR", "UY", "VE",
    },
    "AN": {"AQ", "BV", "GS", "HM", "TF"},
}

COUNTRY_TO_CONTINENT: Dict[str, str] = {}
for _continent_code, _country_codes in _COUNTRY_CODES_BY_CONTINENT.items():
    _continent_name = _CONTINENT_NAME_BY_CODE[_continent_code]
    for _country_code in _country_codes:
        COUNTRY_TO_CONTINENT[_country_code] = _continent_name

# Not assigned in the source table; mostly Pacific territories.
COUNTRY_TO_CONTINENT["UM"] = "Oceania"

def continent_from_country_code(country_code: Optional[str]) -> Optional[str]:
    code = (country_code or "").strip().upper()
    if not code:
        return None
    return COUNTRY_TO_CONTINENT.get(code)
