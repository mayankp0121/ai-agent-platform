import sys
import os
from google_services import GoogleServices
from scheduler_agent import SchedulerAgent
from dotenv import load_dotenv

load_dotenv()

def main():
    if not os.path.exists('credentials.json'):
        print("Error: 'credentials.json' not found in the current directory.")
        print("Please follow these steps:")
        print("1. Go to Google Cloud Console (https://console.cloud.google.com/)")
        print("2. Create a project and enable 'Google Calendar API' and 'Gmail API'.")
        print("3. Go to 'APIs & Services' > 'Credentials'.")
        print("4. Create 'OAuth 2.0 Client IDs' (Desktop app).")
        print("5. Download the JSON and rename it to 'credentials.json'.")
        return

    if not os.getenv("OPENAI_API_KEY"):
        print("Error: OPENAI_API_KEY not found in .env file.")
        return

    try:
        print("--- Initializing Google Services ---")
        google_services = GoogleServices()
        
        agent = SchedulerAgent(google_services)
        
        if len(sys.argv) > 1:
            user_prompt = " ".join(sys.argv[1:])
        else:
            print("\nMeeting Scheduler Agent (GPT-4o)")
            print("Type 'exit' to quit.")
            user_prompt = input("\nWhat meeting should I schedule? > ")
            if user_prompt.lower() == 'exit':
                return

        print("\n--- Processing Request ---")
        response = agent.process_prompt(user_prompt)
        print(f"\nAgent: {response}")

    except Exception as e:
        print(f"\nAn error occurred: {e}")

if __name__ == "__main__":
    main()
