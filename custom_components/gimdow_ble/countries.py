"""Country list for the Gimdow BLE integration (Tuya cloud region selection)."""

from __future__ import annotations

from dataclasses import dataclass

from tuya_iot import TuyaCloudOpenAPIEndpoint

# https://developer.tuya.com/en/docs/iot/oem-app-data-center-distributed?id=Kafi0ku9l07qb


@dataclass
class Country:
    """Describe a supported country."""

    name: str
    country_code: str
    endpoint: str = TuyaCloudOpenAPIEndpoint.AMERICA


# Endpoint shorthands for the compact country table below
_EU = TuyaCloudOpenAPIEndpoint.EUROPE
_AM = TuyaCloudOpenAPIEndpoint.AMERICA
_CN = TuyaCloudOpenAPIEndpoint.CHINA
_IN = TuyaCloudOpenAPIEndpoint.INDIA

# fmt: off
_COUNTRIES_DATA: list[tuple[str, str, str]] = [
    # (name, country_code, endpoint)
    ("Afghanistan", "93", _EU), ("Albania", "355", _EU), ("Algeria", "213", _EU),
    ("American Samoa", "1-684", _EU), ("Andorra", "376", _EU), ("Angola", "244", _EU),
    ("Anguilla", "1-264", _EU), ("Antarctica", "672", _AM),
    ("Antigua and Barbuda", "1-268", _EU), ("Argentina", "54", _AM),
    ("Armenia", "374", _EU), ("Aruba", "297", _EU), ("Australia", "61", _EU),
    ("Austria", "43", _EU), ("Azerbaijan", "994", _EU), ("Bahamas", "1-242", _EU),
    ("Bahrain", "973", _EU), ("Bangladesh", "880", _EU), ("Barbados", "1-246", _EU),
    ("Belarus", "375", _EU), ("Belgium", "32", _EU), ("Belize", "501", _EU),
    ("Benin", "229", _EU), ("Bermuda", "1-441", _EU), ("Bhutan", "975", _EU),
    ("Bolivia", "591", _AM), ("Bosnia and Herzegovina", "387", _EU),
    ("Botswana", "267", _EU), ("Brazil", "55", _AM),
    ("British Indian Ocean Territory", "246", _AM),
    ("British Virgin Islands", "1-284", _EU), ("Brunei", "673", _EU),
    ("Bulgaria", "359", _EU), ("Burkina Faso", "226", _EU), ("Burundi", "257", _EU),
    ("Cambodia", "855", _EU), ("Cameroon", "237", _EU), ("Canada", "1", _AM),
    ("Capo Verde", "238", _EU), ("Cayman Islands", "1-345", _EU),
    ("Central African Republic", "236", _EU), ("Chad", "235", _EU),
    ("Chile", "56", _AM), ("China", "86", _CN),
    ("Christmas Island", "61", _AM), ("Cocos Islands", "61", _AM),
    ("Colombia", "57", _AM), ("Comoros", "269", _EU), ("Cook Islands", "682", _AM),
    ("Costa Rica", "506", _EU), ("Croatia", "385", _EU), ("Cuba", "53", _AM),
    ("Curacao", "599", _AM), ("Cyprus", "357", _EU), ("Czech Republic", "420", _EU),
    ("Democratic Republic of the Congo", "243", _EU), ("Denmark", "45", _EU),
    ("Djibouti", "253", _EU), ("Dominica", "1-767", _EU),
    ("Dominican Republic", "1-809", _AM), ("East Timor", "670", _AM),
    ("Ecuador", "593", _AM), ("Egypt", "20", _EU), ("El Salvador", "503", _EU),
    ("Equatorial Guinea", "240", _EU), ("Eritrea", "291", _EU),
    ("Estonia", "372", _EU), ("Ethiopia", "251", _EU),
    ("Falkland Islands", "500", _AM), ("Faroe Islands", "298", _EU),
    ("Fiji", "679", _EU), ("Finland", "358", _EU), ("France", "33", _EU),
    ("French Polynesia", "689", _EU), ("Gabon", "241", _EU), ("Gambia", "220", _EU),
    ("Georgia", "995", _EU), ("Germany", "49", _EU), ("Ghana", "233", _EU),
    ("Gibraltar", "350", _EU), ("Greece", "30", _EU), ("Greenland", "299", _EU),
    ("Grenada", "1-473", _EU), ("Guam", "1-671", _EU), ("Guatemala", "502", _AM),
    ("Guernsey", "44-1481", _AM), ("Guinea", "224", _AM),
    ("Guinea-Bissau", "245", _AM), ("Guyana", "592", _EU), ("Haiti", "509", _EU),
    ("Honduras", "504", _EU), ("Hong Kong", "852", _AM), ("Hungary", "36", _EU),
    ("Iceland", "354", _EU), ("India", "91", _IN), ("Indonesia", "62", _AM),
    ("Iran", "98", _AM), ("Iraq", "964", _EU), ("Ireland", "353", _EU),
    ("Isle of Man", "44-1624", _AM), ("Israel", "972", _EU), ("Italy", "39", _EU),
    ("Ivory Coast", "225", _EU), ("Jamaica", "1-876", _EU), ("Japan", "81", _AM),
    ("Jersey", "44-1534", _AM), ("Jordan", "962", _EU), ("Kazakhstan", "7", _EU),
    ("Kenya", "254", _EU), ("Kiribati", "686", _AM), ("Kosovo", "383", _AM),
    ("Kuwait", "965", _EU), ("Kyrgyzstan", "996", _EU), ("Laos", "856", _EU),
    ("Latvia", "371", _EU), ("Lebanon", "961", _EU), ("Lesotho", "266", _EU),
    ("Liberia", "231", _EU), ("Libya", "218", _EU), ("Liechtenstein", "423", _EU),
    ("Lithuania", "370", _EU), ("Luxembourg", "352", _EU), ("Macao", "853", _AM),
    ("Macedonia", "389", _EU), ("Madagascar", "261", _EU), ("Malawi", "265", _EU),
    ("Malaysia", "60", _AM), ("Maldives", "960", _EU), ("Mali", "223", _EU),
    ("Malta", "356", _EU), ("Marshall Islands", "692", _EU),
    ("Mauritania", "222", _EU), ("Mauritius", "230", _EU), ("Mayotte", "262", _EU),
    ("Mexico", "52", _AM), ("Micronesia", "691", _EU), ("Moldova", "373", _EU),
    ("Monaco", "377", _EU), ("Mongolia", "976", _EU), ("Montenegro", "382", _EU),
    ("Montserrat", "1-664", _EU), ("Morocco", "212", _EU), ("Mozambique", "258", _EU),
    ("Myanmar", "95", _AM), ("Namibia", "264", _EU), ("Nauru", "674", _AM),
    ("Nepal", "977", _EU), ("Netherlands", "31", _EU),
    ("Netherlands Antilles", "599", _AM), ("New Caledonia", "687", _EU),
    ("New Zealand", "64", _AM), ("Nicaragua", "505", _EU), ("Niger", "227", _EU),
    ("Nigeria", "234", _EU), ("Niue", "683", _AM), ("North Korea", "850", _AM),
    ("Northern Mariana Islands", "1-670", _EU), ("Norway", "47", _EU),
    ("Oman", "968", _EU), ("Pakistan", "92", _EU), ("Palau", "680", _EU),
    ("Palestine", "970", _AM), ("Panama", "507", _EU),
    ("Papua New Guinea", "675", _AM), ("Paraguay", "595", _AM), ("Peru", "51", _AM),
    ("Philippines", "63", _AM), ("Pitcairn", "64", _AM), ("Poland", "48", _EU),
    ("Portugal", "351", _EU), ("Puerto Rico", "1-787, 1-939", _AM),
    ("Qatar", "974", _EU), ("Republic of the Congo", "242", _EU),
    ("Reunion", "262", _EU), ("Romania", "40", _EU), ("Russia", "7", _EU),
    ("Rwanda", "250", _EU), ("Saint Barthelemy", "590", _EU),
    ("Saint Helena", "290", _AM), ("Saint Kitts and Nevis", "1-869", _EU),
    ("Saint Lucia", "1-758", _EU), ("Saint Martin", "590", _EU),
    ("Saint Pierre and Miquelon", "508", _EU),
    ("Saint Vincent and the Grenadines", "1-784", _EU),
    ("Samoa", "685", _EU), ("San Marino", "378", _EU),
    ("Sao Tome and Principe", "239", _AM), ("Saudi Arabia", "966", _EU),
    ("Senegal", "221", _EU), ("Serbia", "381", _EU), ("Seychelles", "248", _EU),
    ("Sierra Leone", "232", _EU), ("Singapore", "65", _EU),
    ("Sint Maarten", "1-721", _AM), ("Slovakia", "421", _EU),
    ("Slovenia", "386", _EU), ("Solomon Islands", "677", _AM),
    ("Somalia", "252", _EU), ("South Africa", "27", _EU),
    ("South Korea", "82", _AM), ("South Sudan", "211", _AM), ("Spain", "34", _EU),
    ("Sri Lanka", "94", _EU), ("Sudan", "249", _AM), ("Suriname", "597", _AM),
    ("Svalbard and Jan Mayen", "4779", _AM), ("Swaziland", "268", _EU),
    ("Sweden", "46", _EU), ("Switzerland", "41", _EU), ("Syria", "963", _AM),
    ("Taiwan", "886", _AM), ("Tajikistan", "992", _EU), ("Tanzania", "255", _EU),
    ("Thailand", "66", _AM), ("Togo", "228", _EU), ("Tokelau", "690", _AM),
    ("Tonga", "676", _EU), ("Trinidad and Tobago", "1-868", _EU),
    ("Tunisia", "216", _EU), ("Turkey", "90", _EU), ("Turkmenistan", "993", _EU),
    ("Turks and Caicos Islands", "1-649", _EU), ("Tuvalu", "688", _EU),
    ("U.S. Virgin Islands", "1-340", _EU), ("Uganda", "256", _EU),
    ("Ukraine", "380", _EU), ("United Arab Emirates", "971", _EU),
    ("United Kingdom", "44", _EU), ("United States", "1", _AM),
    ("Uruguay", "598", _AM), ("Uzbekistan", "998", _EU), ("Vanuatu", "678", _AM),
    ("Vatican", "379", _EU), ("Venezuela", "58", _AM), ("Vietnam", "84", _AM),
    ("Wallis and Futuna", "681", _EU), ("Western Sahara", "212", _EU),
    ("Yemen", "967", _EU), ("Zambia", "260", _EU), ("Zimbabwe", "263", _EU),
]
# fmt: on

TUYA_COUNTRIES = [Country(name, code, ep) for name, code, ep in _COUNTRIES_DATA]
