import os
import json
import re
import csv
from datetime import datetime, timedelta
from openai import OpenAI
from dotenv import load_dotenv
from pypdf import PdfReader

load_dotenv()

class JobAgent:
    def __init__(self, google_services):
        api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("GROQ_API_BASE") or os.getenv("OPENAI_API_BASE")
        self.client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)
        self.google_services = google_services
        self.model = os.getenv("GROQ_MODEL_NAME") or os.getenv("OPENAI_MODEL_NAME") or "gpt-4o"
        self.resume_path = "Mayank_Patidar_Resume.pdf"
        # Ensure we have the tracker sheet ready
        self.spreadsheet_id = self.google_services.get_or_create_tracker_sheet()

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
                to_email=email,
                subject=args['subject'],
                body_text=args['body_text'],
                attachments=attachments
            )
            
            # Log application
            current_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            followup_date = (datetime.now() + timedelta(days=5)).strftime("%Y-%m-%d")
            self.google_services.log_application(
                self.spreadsheet_id, 
                current_date, 
                email, 
                position, 
                "Applied", 
                followup_date
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

    def sync_and_followup(self):
        """
        1. Reads applications from the Google Sheet.
        2. Checks for replies from recruiters. If replied, updates status to 'Replied'.
        3. If no reply, and >= 3 days passed, sends a threaded follow-up email and updates status to 'Followed Up'.
        """
        rows = self.google_services.get_applications(self.spreadsheet_id)
        if not rows or len(rows) <= 1:
            return {"message": "No tracked applications found to sync.", "actions": []}
        
        headers = [h.lower() for h in rows[0]]
        try:
            date_idx = headers.index("date")
            email_idx = headers.index("recruiter email")
            pos_idx = headers.index("position")
            status_idx = headers.index("status")
        except ValueError as e:
            return {"error": f"Invalid spreadsheet headers: {e}"}

        actions = []
        now_dt = datetime.now()

        for idx, row in enumerate(rows[1:], start=2):
            if len(row) <= max(date_idx, email_idx, pos_idx, status_idx):
                continue
            
            app_date_str = row[date_idx]
            recruiter_email = row[email_idx]
            position = row[pos_idx]
            status = row[status_idx]

            try:
                if " " in app_date_str:
                    app_dt = datetime.strptime(app_date_str, "%Y-%m-%d %H:%M:%S")
                else:
                    app_dt = datetime.strptime(app_date_str, "%Y-%m-%d")
            except Exception:
                continue

            if status.lower() in ["interview scheduled", "rejected"]:
                continue

            # Check if recruiter has replied in Gmail
            reply_info = self.google_services.get_recruiter_reply(recruiter_email, app_dt)
            if reply_info:
                thread_id, gmail_msg_id, reply_msg_id, reply_subject, reply_body, is_unread = reply_info
                
                # Analyze the recruiter's reply
                system_prompt = (
                    "You are an AI assistant helping a job candidate, Mayank Patidar, reply to recruiter emails. "
                    "Here is the candidate's personal and professional information:\n"
                    "- Permanent Address: Medahalli, Bengaluru, Karnataka\n"
                    "- Current Location/Address: Bengaluru\n"
                    "- Open for Relocation: Yes\n"
                    "- Expected Salary: 10-12 LPA annual\n"
                    "- Current Salary: 8 LPA annual\n"
                    "- Total Experience: 1.5 years\n"
                    "- Total AI/ML Experience: 1 year\n"
                    "- Candidate's Name: Mayank Patidar\n"
                    "- Candidate's Phone: +91-7000207087\n"
                    f"- Current Local Time: {datetime.now().astimezone().isoformat()}\n\n"
                    "Analyze the recruiter's email and determine if they ask questions, or if they propose or schedule an interview. "
                    "You must respond with a JSON object containing the following keys:\n"
                    "1. 'asks_questions': boolean (true if the recruiter asks any questions or requests details, false otherwise).\n"
                    "2. 'can_answer_all': boolean. Set this to true ONLY if ALL questions asked in the email can be fully and confidently answered using ONLY the candidate's provided information above. If the recruiter asks about anything else (e.g. scheduling a call, technical questions, specific interview slots), set this to false.\n"
                    "3. 'response_draft': string. If 'can_answer_all' is true, write a highly professional, polite, and precise reply email answering the questions. Sign off with: \n"
                    "Best regards,\n"
                    "Mayank Patidar\n"
                    "Phone: +91-7000207087\n"
                    "If 'can_answer_all' is false, leave this empty.\n"
                    "4. 'is_interview': boolean (true if the email represents a scheduled interview, an invitation to schedule an interview, a calendar event invitation, proposing slots, or contains a meeting/interview link).\n"
                    "5. 'interview_details': object or null. If 'is_interview' is true, include:\n"
                    "   - 'is_confirmed': boolean (true if the recruiter has scheduled/confirmed a single specific date and time for the interview in this mail, e.g. 'our call is scheduled for June 18th at 3pm'. Set to false if they propose multiple options or send a booking link like Calendly).\n"
                    "   - 'start_time_iso': string or null. If 'is_confirmed' is true, convert the confirmed date and time into a precise ISO 8601 string, resolving relative references using the Current Local Time context.\n"
                    "   - 'meet_link': string or null. Any Google Meet, Zoom, MS Teams, or calendar scheduling link found in the email.\n"
                    "   - 'duration_minutes': number (default to 30).\n"
                    "   - 'summary': string or null. Suggest a calendar event title (e.g., 'Interview: [Company Name] - Senior Data Engineer').\n"
                    "6. 'reason': string. A brief explanation of why you classified it this way."
                )
                user_prompt = f"Recruiter Email Body:\n{reply_body}\n\nAnalyze this email and return JSON."
                
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages,
                        response_format={"type": "json_object"}
                    )
                    result_json = json.loads(response.choices[0].message.content)
                except Exception as e:
                    print(f"Error parsing LLM reply analysis: {e}")
                    result_json = {"asks_questions": True, "can_answer_all": False, "is_interview": False, "reason": "Failed to parse model response"}

                status_clean = status.strip().lower()
                should_process_reply = (status_clean in ["applied", "followed up", "followed up (day 3)", "followed up (day 7)", "followed up (day 14)"]) or is_unread

                if result_json.get("is_interview"):
                    details = result_json.get("interview_details") or {}
                    if details.get("is_confirmed") and details.get("start_time_iso"):
                        summary = details.get("summary") or f"Interview with {recruiter_email}"
                        start_time_iso = details.get("start_time_iso")
                        duration = details.get("duration_minutes") or 30
                        meet_link = details.get("meet_link")
                        desc = f"Recruiter Email:\n{reply_body}"
                        
                        if self.google_services.is_interview_already_scheduled(start_time_iso, recruiter_email):
                            self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Interview Scheduled")
                            actions.append(f"Interview for {recruiter_email} is already scheduled in Google Calendar. Updated status to 'Interview Scheduled'.")
                        else:
                            event_res = self.google_services.create_recruiter_interview_event(
                                summary=summary,
                                start_time_iso=start_time_iso,
                                duration_minutes=duration,
                                recruiter_email=recruiter_email,
                                description=desc,
                                location_link=meet_link
                            )
                            
                            if "error" not in event_res:
                                self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Interview Scheduled")
                                self.google_services.mark_email_as_read(gmail_msg_id)
                                actions.append(f"Scheduled interview on calendar for {recruiter_email} and updated status to 'Interview Scheduled'.")
                            else:
                                self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Priority Check", color="red")
                                actions.append(f"Detected interview scheduled for {recruiter_email} but calendar creation failed: {event_res.get('error')}.")
                    else:
                        self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Interview Proposed")
                        actions.append(f"Recruiter from {recruiter_email} proposed interview slots or sent a scheduling link. Status updated to 'Interview Proposed'.")
                
                elif should_process_reply:
                    if result_json.get("asks_questions"):
                        if result_json.get("can_answer_all") and result_json.get("response_draft"):
                            # Send threaded response
                            self.google_services.send_threaded_reply(
                                to_email=recruiter_email,
                                subject=reply_subject,
                                body_text=result_json["response_draft"],
                                thread_id=thread_id,
                                original_message_id=reply_msg_id
                            )
                            self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Replied & Answered")
                            self.google_services.mark_email_as_read(gmail_msg_id)
                            actions.append(f"Auto-replied to {recruiter_email}'s questions and updated status to 'Replied & Answered'.")
                        else:
                            # Set to priority check in red
                            self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Priority Check", color="red")
                            actions.append(f"Marked {recruiter_email} as 'Priority Check' (recruiter asked complex questions).")
                    else:
                        self.google_services.update_application_status_with_format(self.spreadsheet_id, idx, "Replied")
                        self.google_services.mark_email_as_read(gmail_msg_id)
                        actions.append(f"Updated {recruiter_email} status to 'Replied' (recruiter responded but no questions asked).")
                continue

            # Check if 3, 7, or 14 days have passed since application, and find next stage
            days_passed = (now_dt - app_dt).days
            
            followup_stage = None
            next_status = None
            
            status_clean = status.strip().lower()
            if status_clean == "applied":
                if days_passed >= 3:
                    followup_stage = 3
                    next_status = "Followed Up (Day 3)"
            elif status_clean in ["followed up (day 3)", "followed up"]:
                if days_passed >= 7:
                    followup_stage = 7
                    next_status = "Followed Up (Day 7)"
            elif status_clean == "followed up (day 7)":
                if days_passed >= 14:
                    followup_stage = 14
                    next_status = "Followed Up (Day 14)"

            if followup_stage is not None:
                thread_id, msg_id, subject = self.google_services.get_last_sent_message(recruiter_email, position)
                
                if followup_stage == 3:
                    prompt_msg = "Checking in regarding my application."
                elif followup_stage == 7:
                    prompt_msg = "Just wanted to follow up and reiterate my interest."
                else:
                    prompt_msg = "Would appreciate any updates regarding my application."

                system_prompt = (
                    "You are an expert career advisor. Generate a very brief, polite, and precise follow-up email. "
                    "Do not ask for anything else, just follow up for the application. "
                    "In the sign-off, you MUST include the following contact information:\n"
                    "Best regards,\n"
                    "Mayank Patidar\n"
                    "Phone: +91-7000207087"
                )
                user_prompt = (
                    f"Recruiter Email: {recruiter_email}\n"
                    f"Position: {position}\n"
                    f"Days since applied: {days_passed}\n"
                    f"Original Subject: {subject}\n\n"
                    f"Please generate a professional email with the core message: '{prompt_msg}'"
                )
                
                messages = [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ]
                
                try:
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=messages
                    )
                    followup_body = response.choices[0].message.content
                except Exception as e:
                    print(f"Error generating follow-up text: {e}")
                    followup_body = (
                        f"Dear Recruiter,\n\n"
                        f"I hope you are doing well. {prompt_msg}\n\n"
                        f"Best regards,\n"
                        f"Mayank Patidar\n"
                        f"+91-7000207087"
                    )

                if thread_id and msg_id:
                    self.google_services.send_threaded_reply(
                        to_email=recruiter_email,
                        subject=subject,
                        body_text=followup_body,
                        thread_id=thread_id,
                        original_message_id=msg_id
                    )
                    action_msg = f"Sent threaded Day {followup_stage} follow-up to {recruiter_email} for '{position}'."
                else:
                    self.google_services.send_confirmation_email(
                        to_email=recruiter_email,
                        subject=f"Follow-up: Application for {position}",
                        body_text=followup_body
                    )
                    action_msg = f"Sent standard Day {followup_stage} follow-up to {recruiter_email} for '{position}'."

                self.google_services.update_application_status(self.spreadsheet_id, idx, next_status)
                actions.append(action_msg)

        return {"actions": actions, "message": f"Sync complete. Processed {len(rows)-1} applications."}
