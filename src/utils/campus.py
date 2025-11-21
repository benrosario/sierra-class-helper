"""
Campus identification utilities.

This module provides functions to identify which Sierra College campus
a course is located at based on building codes.
"""

def get_campus(building: str) -> str:
    """
    Determine which campus based on building code.

    NOTE: Roseville Campus is CLOSED and should never be returned.
    Sierra College has TWO active campuses: Rocklin and Nevada County.

    Args:
        building: Building code from course data

    Returns:
        Campus name as a string

    Campus Identification Rules:
        - Online: Building contains "ONLINE"
        - Off-Campus: High schools, fire stations, parks, golf courses, fairgrounds, correctional facilities
        - Nevada County Campus: Buildings starting with N followed by digits (e.g., N100, N234)
        - Rocklin Campus: Single letter buildings (D, E, F, G, Q, R, S, T, V, W)
        - Unknown: Cannot determine campus from building code

    Examples:
        >>> get_campus('D-101')
        'Rocklin Campus'
        >>> get_campus('N100')
        'Nevada County Campus (Grass Valley/Tahoe-Truckee)'
        >>> get_campus('ONLINE')
        'Online'
    """
    if not building:
        return "Unknown Campus"

    building_upper = building.upper()

    # Online
    if "ONLINE" in building_upper:
        return "Online"

    # Off-campus locations
    if any(keyword in building_upper for keyword in ["HIGH SCHOOL", "FIRE", "PARK", "GOLF", "FAIRGROUNDS", "CORRECTIONAL"]):
        return "Off-Campus Location"

    # Nevada County Campus (Grass Valley/Tahoe-Truckee) - buildings start with N followed by digits
    if building_upper.startswith("N") and len(building_upper) > 1 and building_upper[1].isdigit():
        return "Nevada County Campus (Grass Valley/Tahoe-Truckee)"

    # Rocklin Campus (main campus) - Single letter buildings
    # Common buildings: D, E, F, G, Q, S (Sewell Hall), T, V, W
    # Also includes St buildings (St-1, St-3 are Science Labs at Rocklin)
    # Also includes At buildings (At-2, etc. at Rocklin)
    # Also includes RN buildings (Nursing, at Rocklin)
    if len(building) > 0 and building_upper[0] in "DEFGQRSTVW":
        return "Rocklin Campus"

    # Unknown - cannot determine campus
    return "Unknown Campus"
