from src.scraper.scraper import sierra_scrape

TERM_MAP = {
    #"spring2026": "#select2-result-label-3"
    "spring2026",
    #"summer2026": "#select2-result-label-2"
    "summer2026"
}

running = True
debug = False

print("Sierra Class Scraper - v1.0")
print("Author - Ben Rosario")
print("\n'exit' to escape.")
print("Type help for available terms.")

while running:
    user_input = input(f"\nenter term: ").strip().lower()
    
    if user_input in TERM_MAP:
        sierra_scrape(user_input, debug)
        break
    
    elif user_input == "debug":
        debug = not debug
        print(f"Debug mode: {debug}")
    
    elif user_input == "help":
        print("\nType 'debug' to toggle debug mode\n")
        
        print(f"Allowed parameters: ")
        for term in TERM_MAP:
            print(f"{term}")
    
    elif user_input == "exit":
        break
    
    else:
        print("Invalid input")
    