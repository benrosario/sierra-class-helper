from embeddings import course_to_text

name = "Smith, James R."
meetings1 = [
        {
          "days": "TTh",
          "begin": "1015",
          "end": "1220",
          "building": "V - Math &amp; Technology Center",
          "room": "303",
          "startDate": "08/18/2025",
          "endDate": "12/13/2025"
        }
      ]

meetings2 = [
        {
          "days": "",
          "begin": None,
          "end": None,
          "building": "Online",
          "room": None,
          "startDate": "08/18/2025",
          "endDate": "12/13/2025"
        }
      ]

meetings3 = [
        {
          "days": "MW",
          "begin": "1015",
          "end": "1220",
          "building": "V - Math &amp; Technology Center",
          "room": "305",
          "startDate": "08/18/2025",
          "endDate": "12/13/2025"
        }
      ]

def informalName(name: str):
    informal = name.split(',')
    last = informal[0]
    first = informal[1].strip()
    return f'{first} {last}'    

def meetingDays(data: list[dict[str,str]]):
    days = [
        f'{item['days']}'
        for item in data
    ]
    
    print(days)
    formattedDays = []
    
    # days = ['TTh', 'TTh']
    for day_str in days:
        for char_idx,char in enumerate(day_str):
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
    
course1 = {
    "crn": "83588",
    "course": {
      "source_term": "Fall 2025",
      "CRN": "83588",
      "subject": "MATH",
      "courseNumber": "0027",
      "courseTitle": "Trigonometry",
      "subjectDescription": "Mathematics",
      "credits": 4,
      "enrollment": {
        "max": 35,
        "enrolled": 31,
        "available": 4,
        "waitCapacity": 20,
        "waitCount": 0
      },
      "instructionMethod": "Classroom",
      "faculty": [
        {
          "name": "Wu, Ian",
          "email": "iwu@sierracollege.edu"
        }
      ],
      "meetings": [
        {
          "days": "TTh",
          "begin": "1015",
          "end": "1220",
          "building": "V - Math &amp; Technology Center",
          "room": "303",
          "startDate": "08/18/2025",
          "endDate": "12/13/2025"
        }
      ],
      "attributes": [
        "AA/AS - Comm &amp; Analyt Thinking",
        "AA/AS - Mathematical Skills",
        "CSUGE - B4 Math/Quan Reasoning",
        "Sect No-Cost Open Ed Resources",
        "Zero Textbook Costs"
      ]
    }
  }    

#print(meetingDays(meetings1))
print(course_to_text(course1["course"]))



# print(f'{informalName(name)}')