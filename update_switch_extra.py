import re

with open("custom_components/gimdow_ble/switch.py", "r") as f:
    content = f.read()

content = content.replace("_VirtualAutoLockExtraData", "_SwitchExtraData")

# Move the class definition up
class_def = """@dataclass
class _SwitchExtraData(ExtraStoredData):
    \"\"\"Persisted independently of entity availability.

    This switch's state lives only in HA (never on the device), but its
    availability still follows BLE connectivity. A lock that's disconnected
    (past its grace period) when HA stops would dump state="unavailable" —
    parsing plain .state on restore would silently discard it.
    \"\"\"

    is_on: bool

    def as_dict(self) -> dict[str, Any]:
        return {"is_on": self.is_on}

    @classmethod
    def from_dict(cls, restored: dict[str, Any]) -> _SwitchExtraData | None:
        try:
            return cls(bool(restored["is_on"]))
        except KeyError:
            return None
"""

content = re.sub(r'@dataclass\nclass _SwitchExtraData\(ExtraStoredData\):.*?return None\n', '', content, flags=re.DOTALL)

# Insert it before GimdowBLESwitch
content = content.replace("class GimdowBLESwitch", class_def + "\n\nclass GimdowBLESwitch")

with open("custom_components/gimdow_ble/switch.py", "w") as f:
    f.write(content)
