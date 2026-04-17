import os
import json
import re
import csv
from datetime import datetime
from openai import OpenAI
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()

class JobAgent:
    def __init__(self, google_services):
        self.client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
        self.google_services = google_services
        self.model = "gpt-4o"
        self.resume_path = "Mayank_Patidar_Resume.pdf"

    def _get_resume_text(self):
        """Extracts text from the local resume PDF."""
        if not os.path.exists(self.resume_path):
            return "Resume file not found."
        try:
            reader = PdfReader(self.resume_path)
            text = ""
            for page in reader.pages:
                text += page.extract_text() + "\n"
            return text
        except Exception as e:
            return f"Error reading resume: {e}"

    def _get_tools(self):
        return [
            {
                "type": "function",
                "function": {
                    "name": "send_job_application",
                    "description": "Send a personalized job application email to a recruiter.",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "to_email": {"type": "string", "description": "Recruiter's email address"},
                            "subject": {"type": "string", "description": "Compelling subject line"},
                            "body_text": {"type": "string", "description": "The full cover letter body."}
                        },
                        "required": ["to_email", "subject", "body_text"]
                    }
                }
            }
        ]

    def _send_single(self, email, position, mode, custom_letter, resume_context, attachment_path):
        """Helper to send a single personalized email."""
        if mode == "generate":
            system_prompt = "You are an expert career coach. Write a highly personalized application email based on the provided resume context."
            user_prompt = f"Resume Context: {resume_context}\n\nPosition: {position}\nRecruiter: {email}\n\nGenerate and send a professional application."
        else:
            system_prompt = (
                "You are an assistant that specializes in personalizing cover letters. "
                "1. Use the 'Existing Letter' provided by the user. "
                "2. Replace {name} with the recruiter's first name (inferred from the email if possible, e.g., 'john.doe@company.com' -> 'John'). If not possible, use 'Recruiter'. "
                "3. Replace {position} with the target position provided. "
                "4. Ensure the tone remains professional and identical to the original."
            )
            user_prompt = f"Existing Letter Template:\n{custom_letter}\n\nTarget Position: {position}\nRecruiter Email: {email}\n\nPersonalize and send this email."

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=self._get_tools(),
            tool_choice={"type": "function", "function": {"name": "send_job_application"}}
        )

        response_message = response.choices[0].message
        if response_message.tool_calls:
            tool_call = response_message.tool_calls[0]
            args = json.loads(tool_call.function.arguments)
            
            attachments = [attachment_path] if attachment_path else []
            self.google_services.send_confirmation_email(
                to_email=args['to_email'],
                subject=args['subject'],
                body_text=args['body_text'],
                attachments=attachments
            )
            return True
        return False

    def process_bulk_applications(self, emails_text, position, mode="generate", custom_letter="", attachment_path=None):
        email_list = re.findall(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}', emails_text)
        if not email_list: return "No valid email addresses found."

        resume_context = self._get_resume_text() if mode == "generate" else ""
        results = []
        for email in email_list:
            success = self._send_single(email, position, mode, custom_letter, resume_context, attachment_path)
            results.append(f"{'Sent' if success else 'Failed'} to {email}")
        
        return f"Batch Complete: {', '.join(results)}"

    def process_csv_applications(self, csv_path, mode="generate", custom_letter="", attachment_path=None):
        """Parses CSV and sends personalized emails based on row data."""
        if not os.path.exists(csv_path): return "CSV file not found."
        
        resume_context = self._get_resume_text() if mode == "generate" else ""
        results = []
        
        try:
            with open(csv_path, mode='r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                # Handle potential case-insensitive column names
                headers = {h.lower(): h for h in reader.fieldnames}
                email_col = headers.get('email') or reader.fieldnames[0]
                pos_col = headers.get('position') or reader.fieldnames[1]

                for row in reader:
                    email = row.get(email_col)
                    position = row.get(pos_col)
                    if email and position:
                        success = self._send_single(email, position, mode, custom_letter, resume_context, attachment_path)
                        results.append(f"{'Sent' if success else 'Failed'} to {email} ({position})")
            
            return f"CSV Batch Complete: {', '.join(results)}"
        except Exception as e:
            return f"Error processing CSV: {e}"
