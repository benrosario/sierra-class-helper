from openai import OpenAI
from embeddings import search_courses, course_to_text

client = OpenAI()

print("Type 'quit' or 'exit' to close program.")

while True:
    user_input = input("User: ")
    if user_input.lower() in ["quit", "exit"]:
        break
    
    # Use FAISS 
    results = search_courses(user_input, k=3)
    
    # Use course_to_text to feed LLM context
    context = "\n".join(course_to_text(r) for r in results)
    
    # Ask LLM to format responses
    completion = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[
            {"role": "system", "content": "You are a helpful academic advisor named Sierra Class Helper, created by student Ben Rosario."},
            {"role": "system", "content": "Pay extreme attention to requested dates and times to ensure good responses for users."},
            {"role": "system", "content": "You are helping student from the California Community College 'Sierra College'. Their website is https://sierracollege.edu."},
            {"role": "system", "content": "You can advise students on any academic matter, grabbing information from the Sierra College website."},
            {"role": "system", "content": "Make sure to show class start and end dates, and comment on whether or not the class has begun instruction by checking the current date."},
            {"role": "system", "content": "Make sure to format enrollment numbers as fractions for a cleaner presentation."},
            {"role": "system", "content": "If the user doesn't ask for/about classes, do not show them."},
            {"role": "system", "content": "Change dates from 'M' to 'Monday' and so on, also change times from '900' to '9am', for example."},
            {"role": "system", "content": "Make sure to give the building letter for classes, not just the building name. Make sure to append the building letter directly next to the room number, e.g. V103."},
            {"role": "user", "content": f"User query: {user_input}\nHere are matching courses:\n{context}\n\nPlease suggest the most relevant options OR respond to a question."}
        ]
    )
    
    print("\n\nSierra Class Helper:", completion.choices[0].message.content, end="\n\n")