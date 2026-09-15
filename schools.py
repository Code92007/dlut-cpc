"""School aliases and the independently maintained CPC groups."""
from __future__ import annotations

import re

SCHOOL_GROUPS = {
    "大连理工大学": "大连理工大学",
    "大连理工大学软件学院": "大连理工大学",
    "大连理工大学开发区校区": "大连理工大学",
    "大连理工大学城市学院": "大连理工大学城市学院",
    "大连理工大学盘锦校区": "大连理工大学盘锦校区",
    "大连理工大学盘锦学院": "大连理工大学盘锦校区",
    "大连理工大学（盘锦校区）": "大连理工大学盘锦校区",
    "大连理工大学(盘锦校区)": "大连理工大学盘锦校区",
    "Dalian University of Technology": "大连理工大学",
    "Dalian University of Technology, School of Software": "大连理工大学",
    "School of Software Technology, Dalian University of Technology": "大连理工大学",
    "City Institute, Dalian University of Technology": "大连理工大学城市学院",
    "Dalian University of Technology, Panjin Campus": "大连理工大学盘锦校区",
}
SCHOOL_ALIASES = set(SCHOOL_GROUPS)
MAINTENANCE_GROUPS = ("大连理工大学", "大连理工大学城市学院", "大连理工大学盘锦校区")


def school_group(name: str) -> str | None:
    normalized = re.sub(r"[\s（）(),，·]+", "", name).casefold()
    return next((group for alias, group in SCHOOL_GROUPS.items()
                 if re.sub(r"[\s（）(),，·]+", "", alias).casefold() == normalized), None)
