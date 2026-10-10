"""Canonical display names for identifiable XCPC onsite contests."""
from __future__ import annotations

import re


SITES = {
    "anshan": "鞍山", "beijing": "北京", "changchun": "长春", "changsha": "长沙",
    "chengdu": "成都", "chongqing": "重庆", "dalian": "大连", "fuzhou": "福州",
    "guangzhou": "广州", "guilin": "桂林", "haerbin": "哈尔滨", "harbin": "哈尔滨",
    "hangzhou": "杭州", "hefei": "合肥", "hongkong": "香港", "jiaozuo": "焦作",
    "jilin": "吉林", "jinan": "济南", "jinhua": "金华", "kunming": "昆明",
    "mianyang": "绵阳", "mudanjiang": "牡丹江", "nanchang": "南昌", "nanjing": "南京",
    "nanning": "南宁", "nanyang": "南阳", "ningbo": "宁波", "qingdao": "青岛",
    "qinhuangdao": "秦皇岛", "shanghai": "上海", "shenyang": "沈阳", "shenzhen": "深圳",
    "taipei": "台北", "tianjin": "天津", "urumchi": "乌鲁木齐", "urumqi": "乌鲁木齐",
    "weihai": "威海", "wuhan": "武汉", "xiamen": "厦门", "xian": "西安",
    "xuzhou": "徐州", "yinchuan": "银川", "zhengzhou": "郑州",
}
EXCLUDED = re.compile(
    r"网络|选拔|预选|女生|女子|高职|热身|省赛|省竞赛|邀请赛|地区赛|挑战赛|"
    r"preliminary|invitational|women|girls|online|warm.?up|world|全球|世界", re.I
)


def canonical_contest_name(event: str, *, series: str = "", contest_id: str = "", location: str = "") -> str:
    """Use the source season or explicit edition, never the actual event date.

    Unknown contests and other contest types keep their original names. Historical
    source IDs identify sites even when the title contains only a host university.
    """
    if EXCLUDED.search(event):
        return event
    identity = re.fullmatch(r"(icpc|ccpc)((?:19|20)\d{2})[:_-]?(.+)", str(contest_id or ""), re.I)
    edition = re.search(r"第\s*(\d+)\s*届|\b(\d+)(?:st|nd|rd|th)\b", event, re.I)
    number = int(edition[1] or edition[2]) if edition else None
    site = ""
    if identity:
        series = identity[1].upper()
        season = int(identity[2])
        suffix = re.sub(r"[\s_-]", "", identity[3]).casefold()
        site = SITES.get(suffix, "")
        if series == "CCPC" and suffix in {"final", "总决赛"}:
            site = "总决赛"
        elif series == "ICPC" and suffix in {"ecfinal", "cnfinal"}:
            site = "东亚区决赛" if suffix == "ecfinal" else "中国区决赛"
        elif identity[3] in SITES.values():
            site = identity[3]
        expected = season - (2014 if series == "CCPC" else 1975)
        if number is not None and number != expected:
            return event
        number = expected
    else:
        if re.search(r"CCPC|(?<!国际)中国大学生程序设计竞赛", event, re.I):
            series = "CCPC"
        elif re.search(r"ICPC|国际大学生程序设计竞赛", event, re.I):
            series = "ICPC"
        else:
            return event
        if number is None:
            year = re.search(r"(?:19|20)\d{2}", event)
            if not year:
                return event
            number = int(year[0]) - (2014 if series == "CCPC" else 1975)
    if series not in {"ICPC", "CCPC"} or number is None or not 1 <= number <= 150:
        return event
    if not site:
        # The Chinese host suffix is evidence, not part of the contest name.
        title = re.split(r"\s+（", event, maxsplit=1)[0]
        if series == "CCPC" and re.search(r"总决赛|final", title, re.I):
            site = "总决赛"
        elif series == "ICPC" and re.search(r"东亚区决赛|(?:east|ec)[ -]?(?:continent|league)?[ -]?final", title, re.I):
            site = "东亚区决赛"
        elif series == "ICPC" and re.search(r"中国区决赛|(?:china|cn)[ -]?final", title, re.I):
            site = "中国区决赛"
        elif not re.search(r"final|决赛", title, re.I):
            sites = {name for slug, name in SITES.items()
                     if name in title or re.search(r"\b" + slug + r"\b", title, re.I)}
            if len(sites) == 1:
                site = sites.pop()
            elif not sites and location in SITES.values():
                site = location
    if not site:
        return event
    if series == "CCPC":
        return f"第 {number} 届 CCPC 中国大学生程序设计竞赛{site}" + ("" if site == "总决赛" else "站")
    suffix = site if site in {"东亚区决赛", "中国区决赛"} else f"亚洲区域赛{site}站"
    return f"第 {number} 届 ICPC 国际大学生程序设计竞赛{suffix}"


def honor_contest_name(record: dict) -> str:
    return canonical_contest_name(
        str(record.get("event") or ""), series=record.get("series", ""),
        contest_id=record.get("externalContestId", ""), location=record.get("location", ""),
    )


def canonical_ranklists(contests: dict[str, str]) -> dict[str, str]:
    # Keep old aliases available to cached consumers as well as canonical names.
    return {**contests, **{canonical_contest_name(name): url for name, url in contests.items()}}
