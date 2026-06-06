"""
Shared utility functions for course data formatting.

This module contains functions used across multiple files to format
course-related data consistently.
"""

def informalName(name: str) -> str:
    """
    Convert formal name format 'Last, First' to informal 'First Last'.

    Args:
        name: Name in format 'Last, First'

    Returns:
        Name in format 'First Last'

    Example:
        >>> informalName('Doe, John')
        'John Doe'
    """
    informal = name.split(',')
    last = informal[0]
    first = informal[1].strip()
    return f'{first} {last}'


def meetingDays(data: list[dict[str, str]]) -> list[str]:
    """
    Format meeting days from abbreviations to full day names.

    Converts day codes like 'MW', 'TTh' to ['Monday', 'Wednesday'], ['Tuesday', 'Thursday'].

    Args:
        data: List of meeting dictionaries containing 'days' field

    Returns:
        List of formatted day names (no duplicates)

    Example:
        >>> meetingDays([{'days': 'MW'}, {'days': 'TTh'}])
        ['Monday', 'Wednesday', 'Tuesday', 'Thursday']
    """
    days = [
        f'{item["days"]}'
        for item in data
    ]

    formattedDays = []

    for day_str in days:
        for char_idx, char in enumerate(day_str):
            match char:
                case "M":
                    if "Monday" not in formattedDays:
                        formattedDays.append("Monday")
                case "W":
                    if "Wednesday" not in formattedDays:
                        formattedDays.append("Wednesday")
                case "T":
                    if (char_idx + 1) < len(day_str) and day_str[char_idx+1] == 'h':
                        if "Thursday" not in formattedDays:
                            formattedDays.append("Thursday")
                    else:
                        if "Tuesday" not in formattedDays:
                            formattedDays.append("Tuesday")
                case "h":
                    pass
                case _:
                    if "Online" not in formattedDays:
                        formattedDays.append("Online")

    return formattedDays


# Short day labels for compact one-line summaries (e.g. the /search command).
_SHORT_DAY = {
    "Monday": "Mon",
    "Tuesday": "Tue",
    "Wednesday": "Wed",
    "Thursday": "Thu",
    "Friday": "Fri",
    "Online": "Online",
}


def format_time(raw: str | None) -> str:
    """
    Turn Banner's 'HHMM' clock string into a readable 12-hour time.

    Banner stores times as zero-padded 24-hour strings ('0900', '1350'). Missing
    or unparseable values come back as 'TBA' so callers can print them as-is.

    Example:
        >>> format_time('0900')
        '9:00am'
        >>> format_time('1350')
        '1:50pm'
        >>> format_time(None)
        'TBA'
    """
    if not raw or not raw.isdigit() or len(raw) not in (3, 4):
        return "TBA"
    hour = int(raw[:-2])
    minute = raw[-2:]
    if not (0 <= hour <= 23):
        return "TBA"
    suffix = "am" if hour < 12 else "pm"
    hour12 = hour % 12 or 12
    return f"{hour12}:{minute}{suffix}"


def summarize_meetings(meetings: list[dict]) -> str:
    """
    Build a one-line, human-readable summary of when/where a course meets.

    Combines the day names from meetingDays() with the time range and room of the
    first meeting, e.g. 'Mon/Wed 9:00am–10:50am in V 303'. Falls back gracefully
    when meeting data is missing.

    Example:
        >>> summarize_meetings([{'days': 'MW', 'begin': '0900', 'end': '1050',
        ...                      'building': 'V', 'room': '303'}])
        'Mon/Wed 9:00am–10:50am in V 303'
    """
    if not meetings:
        return "Meeting time TBA"

    day_names = meetingDays(meetings)
    days = "/".join(_SHORT_DAY.get(d, d) for d in day_names) if day_names else "TBA"

    first = meetings[0]
    begin = format_time(first.get("begin"))
    end = format_time(first.get("end"))
    time_range = f"{begin}–{end}" if begin != "TBA" or end != "TBA" else "TBA"

    building = (first.get("building") or "").strip()
    room = (first.get("room") or "").strip()
    location = " ".join(part for part in (building, room) if part)

    summary = f"{days} {time_range}".strip()
    if location:
        summary += f" in {location}"
    return summary
