import os
import json
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

class SchedulerAgent:
    def __init__(self, google_services):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.google_services = google_services
        self.model = "gpt-4o"

    def _get_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "create_calendar_event",
                    "description": "Create a Google Calendar event with a Google Meet link.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "summary": {"type": "string", "description": "Title of the meeting"},
                            "description": {"type": "string", "description": "Agenda or details of the meeting"},
                            "start_time": {"type": "string", "description": "Start time in ISO 8601 format (e.g., 2023-10-27T10:00:00Z)"},
                            "duration_minutes": {"type": "integer", "description": "Duration of the meeting in minutes"},
                            "attendee_email": {"type": "string", "description": "Email address of the person to invite"}
                        },
                        "required": ["summary", "start_time", "duration_minutes", "attendee_email"]
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "send_confirmation_email",
                    "description": "Send a confirmation email with meeting details and the Google Meet link.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to_email": {"type": "string", "description": "Recipient email address"},
                            "subject": {"type": "string", "description": "Subject of the email"},
                            "body_text": {"type": "string", "description": "Content of the email, including the GMeet link."}
                        },
                        "required": ["to_email", "subject", "body_text"]
                    }
                }
            }
        ]

    def process_prompt(self, user_prompt):
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        messages = [
            {"role": "system", "content": f"You are an expert meeting scheduler agent. Current time is {now}. "
                                          "Your goal is to schedule a meeting and then send a confirmation email. "
                                          "Step 1: Create the calendar event. "
                                          "Step 2: Use the GMeet link from the event to send the email. "
                                          "CRITICAL: In the email body, you MUST include the specific date of the meeting "
                                          "and a professional signature with the sender's name (if not known, use 'The Team'). "
                                          "Always confirm with the user after both steps are completed."},
            {"role": "user", "content": user_prompt}
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self._get_tools(),
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            messages.append(response_message)
            
            # Step 1: Create Calendar Event
            for tool_call in tool_calls:
                if tool_call.function.name == "create_calendar_event":
                    args = json.loads(tool_call.function.arguments)
                    print(f"--- Creating Calendar Event: {args['summary']} ---")
                    result = self.google_services.create_calendar_event(**args)
                    
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call.id,
                        "name": "create_calendar_event",
                        "content": json.dumps(result)
                    })

                    if "meetLink" in result:
                        print(f"--- GMeet Link Generated: {result['meetLink']} ---")
            
            # Step 2: Second completion to handle the email (using the result from step 1)
            second_response = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                tools=self._get_tools()
            )
            
            second_message = second_response.choices[0].message
            if second_message.tool_calls:
                messages.append(second_message)
                for tool_call in second_message.tool_calls:
                    if tool_call.function.name == "send_confirmation_email":
                        args = json.loads(tool_call.function.arguments)
                        print(f"--- Sending Email to {args['to_email']} ---")
                        result = self.google_services.send_confirmation_email(**args)
                        
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tool_call.id,
                            "name": "send_confirmation_email",
                            "content": json.dumps(result)
                        })

                # Final response to the user
                final_response = self.client.chat.completions.create(
                    model=self.model,
                    messages=messages
                )
                return final_response.choices[0].message.content

        return response_message.content
