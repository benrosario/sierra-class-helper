import json
import math
from datetime import datetime
from playwright.sync_api import sync_playwright

global TERM_MAP
TERM_MAP = {
    "spring2025": "#select2-result-label-5",
    "summer2025": "#select2-result-label-4",
    "fall2025": "#select2-result-label-3",
    "spring2026": "#select2-result-label-2"
}

def process_json(course):
    """
    Extract and format relevant fields from raw course API response.

    Takes the full course JSON from Sierra College's API and extracts only
    the fields needed for the chatbot, reformatted into a cleaner structure.

    Args:
        course: Raw course dictionary from the Sierra College API

    Returns:
        dict: Cleaned course data with only relevant fields (term, CRN, subject,
              enrollment, faculty, meetings, etc.)
    """
    return {
        "term": course.get("termDesc"),
        "CRN": course.get("courseReferenceNumber"),
        "subject": course.get("subject"),
        "courseNumber": course.get("courseNumber"),
        "courseTitle": course.get("courseTitle"),
        "subjectDescription": course.get("subjectDescription"),
        "credits": course.get("creditHourLow"),
        "enrollment": {
            "max": course.get("maximumEnrollment"),
            "enrolled": course.get("enrollment"),
            "available": course.get("seatsAvailable"),
            "waitCapacity": course.get("waitCapacity"),
            "waitCount": course.get("waitCount"),
        },
        "instructionMethod": course.get("instructionalMethodDescription"),
        "faculty": [
            {
                "name": f.get("displayName"),
                "email": f.get("emailAddress")
            } for f in course.get("faculty", [])
        ],
        "meetings": [
            {
                "days": "".join(
                    d for d, flag in {
                        "M": mt.get("monday"),
                        "T": mt.get("tuesday"),
                        "W": mt.get("wednesday"),
                        "Th": mt.get("thursday"),
                        "F": mt.get("friday"),
                    }.items() if flag
                ),
                "begin": mt.get("beginTime"),
                "end": mt.get("endTime"),
                "building": mt.get("buildingDescription"),
                "room": mt.get("room"),
                "startDate": mt.get("startDate"),
                "endDate": mt.get("endDate"),
            }
            for m in course.get("meetingsFaculty", [])
            for mt in [m.get("meetingTime", {})]
        ],
        "attributes": [a.get("description") for a in course.get("sectionAttributes", [])],
    }

courses_processed = 0

def sierra_scrape(term: str, debug: bool):
    """
    Scrape course data from Sierra College's course search page.

    Uses Playwright to automate browser interaction with Sierra's course search
    system, intercepts API responses, and collects all course data for the
    specified term.

    Args:
        term: Semester to scrape (e.g., "fall2025", "spring2026")
              Must be a key in TERM_MAP
        debug: If True, runs browser in headed mode (visible) for debugging

    Returns:
        dict: Dictionary mapping CRN to course data
              Format: {CRN: {"course": {...course data...}}}

    Raises:
        ValueError: If term is not found in TERM_MAP

    Example:
        >>> courses = sierra_scrape("fall2025", debug=False)
        >>> print(len(courses))
        1234
    """
    
    print("Initializing...")
    
    if term not in TERM_MAP: 
        raise ValueError(f"Invalid parameter given. \nAllowed parameters: {TERM_MAP}")
    
    term_element = ""
    
    match term:
        case "fall2025":
            term_element = "#select2-result-label-4"
        case "spring2026":
            term_element = "#select2-result-label-3"
        case "summer2026":
            term_element = "#select2-result-label-2"
            
    
    with sync_playwright() as p: 
        isHeadless = not debug
        browser = p.chromium.launch(headless=isHeadless)
        page = browser.new_page()
        
        classJson = {}
        def handle_response(response):
            global courses_processed
            if "ssb/searchResults/searchResults?txt_term=" in response.url:
                try:
                    data = response.json()
                    # Only store the 'data' element
                    if "data" in data:
                        for c in data["data"]:
                            classJson[ c.get("courseReferenceNumber") ] = process_json(c)
                        
                        courses_processed += len(data['data'])
                        
                        percent_complete = (courses_processed/total_classes)*100
                        print(f"{percent_complete:.2f}% complete")
                except Exception as e:
                    print("Failed to parse JSON: ", e)          
        
        
        page.goto("https://ss.sierracollege.edu:8885/StudentRegistrationSsb/ssb/term/termSelection?mode=search")
        
        # Open the dropdown
        page.locator("#select2-chosen-1").click()
        
        # Click on chosen term
        # page.wait_for_timeout(2000)
        page.locator(term_element).click()
        
        # Click 'Continue'
        page.locator("#term-go").click()
        
        # Search for all classes in selected term
        page.locator("#search-go").click()
        
        # We are now looking at all courses for the selected term
        page.wait_for_selector("tbody tr[data-id]")
        page.wait_for_timeout(3000)
        
        total_classes_str = page.locator("span.results-out-of").inner_text()
        total_classes = int(total_classes_str.split()[0])
        total_pages = math.ceil(total_classes / 50)
        
        if debug:
            print(f"Total Pages: {total_pages}")
        
        # Snoop for JSON response
        page.on("response", handle_response)  
        
        # Change courses listed per page
        page.select_option("select.page-size-select", "50")
        page.wait_for_timeout(3000)
        
        
        
        for i in range(total_pages):
            page.wait_for_selector("tbody tr[data-id]", timeout=5000)
            page.wait_for_timeout(900)
            
            if i < total_pages - 1:
                page.locator("button[title='Next']").click()
            page.wait_for_timeout(2000)
        
        if len(classJson) > 0:
                timestamp = datetime.now().astimezone().strftime("%Y-%m-%d_%H-%M-%S%z")
                filepath = f"course_data/{term}_{timestamp}.json"
                
                with open(f"{filepath}", "w", encoding="utf-8") as f:
                    # pretty-print JSON
                    json.dump(classJson, f, indent=2, ensure_ascii=False)
                print(f"Finished. {courses_processed} courses scraped.")
                print(f"JSON saved under {filepath}")
        else:
            print("No classes were found.")
        
        # Pause to inspect the page
        if debug:
            input("Press 'enter' to close...")