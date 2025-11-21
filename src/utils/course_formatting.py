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
