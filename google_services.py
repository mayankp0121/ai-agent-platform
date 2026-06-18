import os
import json
import base64
from email.message import EmailMessage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/gmail.send',
    'https://www.googleapis.com/auth/gmail.modify',
    'https://www.googleapis.com/auth/spreadsheets'
]

class GoogleServices:
    def __init__(self, credentials_path='credentials.json', token_path='token.json', token_data=None, tracker_spreadsheet_id=None, user_email=None):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.token_data = token_data
        self.tracker_spreadsheet_id = tracker_spreadsheet_id
        self.user_email = user_email
        self.creds = self._authenticate()
        
        api_key = os.getenv("GROQ_API_KEY") or os.getenv("OPENAI_API_KEY")
        base_url = os.getenv("GROQ_API_BASE") or os.getenv("OPENAI_API_BASE")
        self.model = os.getenv("GROQ_MODEL_NAME") or os.getenv("OPENAI_MODEL_NAME") or "openai/gpt-oss-120b"
        self.openai_client = OpenAI(api_key=api_key, base_url=base_url) if base_url else OpenAI(api_key=api_key)

    def _get_cache_file(self):
        if self.user_email:
            safe_email = "".join([c if c.isalnum() else "_" for c in self.user_email])
            return f"classified_emails_{safe_email}.json"
        return "classified_emails.json"

    @property
    def calendar_service(self):
        return build('calendar', 'v3', credentials=self.creds)

    @property
    def gmail_service(self):
        return build('gmail', 'v1', credentials=self.creds)

    @property
    def sheets_service(self):
        return build('sheets', 'v4', credentials=self.creds)

    def _authenticate(self):
        creds = None
        if self.token_data:
            try:
                creds = Credentials.from_authorized_user_info(self.token_data, SCOPES)
            except Exception as e:
                print(f"Error loading from token_data: {e}")
        elif os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                try:
                    creds.refresh(Request())
                except Exception as e:
                    print(f"Token refresh failed: {e}. Re-authenticating...")
                    if not os.path.exists(self.credentials_path):
                        raise FileNotFoundError(f"{self.credentials_path} not found. Please provide it from Google Cloud Console.")
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                    creds = flow.run_local_server(port=0)
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(f"{self.credentials_path} not found. Please provide it from Google Cloud Console.")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            
            if not self.token_data:
                with open(self.token_path, 'w') as token:
                    token.write(creds.to_json())
        return creds

    def create_calendar_event(self, summary, start_time, duration_minutes, attendee_email=None, description="", ignore_event_id=None):
        """
        Creates a calendar event if no conflict, otherwise returns available slots.
        """
        from datetime import datetime, timedelta
        
        local_tz = datetime.now().astimezone().tzinfo
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00')).astimezone(local_tz)
        end = start + timedelta(minutes=duration_minutes)

        # Check if requested time is outside office hours (10 AM - 7 PM Monday to Friday)
        is_outside_office_hours = False
        if start.weekday() >= 5:
            is_outside_office_hours = True
        elif start.hour < 10 or (end.hour > 19 or (end.hour == 19 and end.minute > 0)):
            is_outside_office_hours = True

        has_conflict = is_outside_office_hours

        # Only check calendar events if it's not already flagged as outside office hours
        if not has_conflict:
            day_start = start.replace(hour=0, minute=0, second=0, microsecond=0)
            day_end = start.replace(hour=23, minute=59, second=59, microsecond=999999)

            try:
                events_result = self.calendar_service.events().list(
                    calendarId='primary',
                    timeMin=day_start.isoformat(),
                    timeMax=day_end.isoformat(),
                    singleEvents=True,
                    orderBy='startTime'
                ).execute()
                events = events_result.get('items', [])
            except Exception as e:
                print(f"Error fetching calendar events: {e}")
                events = []

            for event in events:
                if event.get('status') == 'cancelled':
                    continue
                if ignore_event_id and event.get('id') == ignore_event_id:
                    continue
                if 'dateTime' not in event.get('start', {}):
                    continue  # Ignore all-day events
                if event.get('transparency') == 'transparent':
                    continue  # Ignore transparent events
                
                evt_start_str = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
                evt_end_str = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
                if not evt_start_str or not evt_end_str:
                    continue
                
                if 'T' not in evt_start_str:
                    evt_start = datetime.fromisoformat(evt_start_str).replace(tzinfo=local_tz)
                    evt_end = datetime.fromisoformat(evt_end_str).replace(tzinfo=local_tz)
                else:
                    evt_start = datetime.fromisoformat(evt_start_str.replace('Z', '+00:00')).astimezone(local_tz)
                    evt_end = datetime.fromisoformat(evt_end_str.replace('Z', '+00:00')).astimezone(local_tz)
                
                # Check conflict with 15-minute buffer before and after the existing event
                if max(start, evt_start - timedelta(minutes=15)) < min(end, evt_end + timedelta(minutes=15)):
                    has_conflict = True
                    break

        if has_conflict:
            available_slots = []
            target_day_local = start
            days_checked = 0
            
            while len(available_slots) < 10 and days_checked < 7:
                while target_day_local.weekday() >= 5:
                    target_day_local += timedelta(days=1)
                
                work_start_local = target_day_local.replace(hour=10, minute=0, second=0, microsecond=0)
                work_end_local = target_day_local.replace(hour=19, minute=0, second=0, microsecond=0)
                
                t_day_start = target_day_local.replace(hour=0, minute=0, second=0, microsecond=0)
                t_day_end = target_day_local.replace(hour=23, minute=59, second=59, microsecond=999999)
                
                try:
                    t_events_result = self.calendar_service.events().list(
                        calendarId='primary',
                        timeMin=t_day_start.isoformat(),
                        timeMax=t_day_end.isoformat(),
                        singleEvents=True,
                        orderBy='startTime'
                    ).execute()
                    t_events = t_events_result.get('items', [])
                except Exception:
                    t_events = []
                
                parsed_events = []
                for event in t_events:
                    if event.get('status') == 'cancelled':
                        continue
                    if ignore_event_id and event.get('id') == ignore_event_id:
                        continue
                    if 'dateTime' not in event.get('start', {}):
                        continue  # Ignore all-day events
                    if event.get('transparency') == 'transparent':
                        continue  # Ignore transparent events
                    
                    evt_start_str = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
                    evt_end_str = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
                    if not evt_start_str or not evt_end_str:
                        continue
                    if 'T' not in evt_start_str:
                        evt_start = datetime.fromisoformat(evt_start_str).replace(tzinfo=local_tz)
                        evt_end = datetime.fromisoformat(evt_end_str).replace(tzinfo=local_tz)
                    else:
                        evt_start = datetime.fromisoformat(evt_start_str.replace('Z', '+00:00')).astimezone(local_tz)
                        evt_end = datetime.fromisoformat(evt_end_str.replace('Z', '+00:00')).astimezone(local_tz)
                    parsed_events.append((evt_start, evt_end))
                
                current_slot_start = work_start_local
                now_local = datetime.now(local_tz)
                
                while current_slot_start + timedelta(minutes=duration_minutes) <= work_end_local:
                    current_slot_end = current_slot_start + timedelta(minutes=duration_minutes)
                    
                    slot_conflict = False
                    for evt_start, evt_end in parsed_events:
                        # Enforce 15-minute buffer check between slot and existing event
                        if max(current_slot_start, evt_start - timedelta(minutes=15)) < min(current_slot_end, evt_end + timedelta(minutes=15)):
                            slot_conflict = True
                            break
                    
                    if current_slot_start > now_local and not slot_conflict:
                        available_slots.append({
                            'start': current_slot_start.isoformat(),
                            'end': current_slot_end.isoformat(),
                            'label': current_slot_start.strftime('%a, %b %d: %I:%M %p') + ' - ' + current_slot_end.strftime('%I:%M %p')
                        })
                    
                    current_slot_start += timedelta(minutes=30)
                
                target_day_local += timedelta(days=1)
                days_checked += 1

            message = 'Requested time is already booked. Here are available slots.' if not is_outside_office_hours else 'Requested time is outside office hours (Mon-Fri, 10 AM - 7 PM). Here are available slots.'

            return {
                'conflict': True,
                'slots': available_slots[:15],
                'message': message,
                'original_params': {
                    'summary': summary,
                    'duration_minutes': duration_minutes,
                    'attendee_email': attendee_email,
                    'description': description
                }
            }

        return self.create_calendar_event_direct(summary, start_time, duration_minutes, attendee_email, description)

    def create_calendar_event_direct(self, summary, start_time, duration_minutes, attendee_email, description=""):
        from datetime import datetime, timedelta
        
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end = start + timedelta(minutes=duration_minutes)

        event = {
            'summary': summary,
            'description': description,
            'start': {
                'dateTime': start.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end.isoformat(),
                'timeZone': 'UTC',
            },
            'attendees': [
                {'email': attendee_email},
            ],
            'conferenceData': {
                'createRequest': {
                    'requestId': f"meeting_{int(datetime.now().timestamp())}",
                    'conferenceSolutionKey': {'type': 'hangoutsMeet'}
                }
            },
            'reminders': {
                'useDefault': True,
            },
        }

        try:
            event = self.calendar_service.events().insert(
                calendarId='primary', 
                body=event, 
                conferenceDataVersion=1
            ).execute()
            
            meet_link = event.get('hangoutLink')
            return {
                'id': event.get('id'),
                'htmlLink': event.get('htmlLink'),
                'meetLink': meet_link
            }
        except HttpError as error:
            return {'error': str(error)}

    def create_recruiter_interview_event(self, summary, start_time_iso, duration_minutes, recruiter_email, description="", location_link=None):
        """
        Creates a calendar event for a scheduled recruiter interview, optionally setting the meeting location link.
        """
        from datetime import datetime, timedelta
        try:
            if '+' in start_time_iso or '-' in start_time_iso[10:]:
                start = datetime.fromisoformat(start_time_iso)
            else:
                cleaned_iso = start_time_iso.replace('Z', '+00:00')
                if '+' not in cleaned_iso and '-' not in cleaned_iso[10:]:
                    local_offset = datetime.now().astimezone().tzinfo
                    start = datetime.fromisoformat(start_time_iso).replace(tzinfo=local_offset)
                else:
                    start = datetime.fromisoformat(cleaned_iso)
            
            end = start + timedelta(minutes=duration_minutes)
            
            event = {
                'summary': summary,
                'description': description,
                'start': {
                    'dateTime': start.isoformat(),
                },
                'end': {
                    'dateTime': end.isoformat(),
                },
                'attendees': [
                    {'email': recruiter_email},
                ],
                'reminders': {
                    'useDefault': True,
                },
            }
            
            if location_link:
                event['location'] = location_link
                if description:
                    event['description'] = f"{description}\n\nMeeting Link: {location_link}"
                else:
                    event['description'] = f"Meeting Link: {location_link}"

            event = self.calendar_service.events().insert(
                calendarId='primary', 
                body=event
            ).execute()
            
            return {
                'id': event.get('id'),
                'htmlLink': event.get('htmlLink'),
                'meetLink': event.get('hangoutLink') or event.get('location')
            }
        except Exception as e:
            print(f"Error creating recruiter interview event: {e}")
            return {'error': str(e)}

    def is_interview_already_scheduled(self, start_time_iso, recruiter_email):
        """
        Checks if an interview event is already scheduled around start_time_iso for recruiter_email.
        """
        from datetime import datetime, timedelta
        try:
            if '+' in start_time_iso or '-' in start_time_iso[10:]:
                start = datetime.fromisoformat(start_time_iso)
            else:
                cleaned_iso = start_time_iso.replace('Z', '+00:00')
                if '+' not in cleaned_iso and '-' not in cleaned_iso[10:]:
                    local_offset = datetime.now().astimezone().tzinfo
                    start = datetime.fromisoformat(start_time_iso).replace(tzinfo=local_offset)
                else:
                    start = datetime.fromisoformat(cleaned_iso)
            
            time_min = (start - timedelta(minutes=15)).isoformat()
            time_max = (start + timedelta(minutes=15)).isoformat()
            
            events_result = self.calendar_service.events().list(
                calendarId='primary',
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True
            ).execute()
            
            events = events_result.get('items', [])
            for event in events:
                summary = event.get('summary', '').lower()
                desc = event.get('description', '').lower()
                attendees = [a.get('email', '').lower() for a in event.get('attendees', [])]
                
                if recruiter_email.lower() in attendees or recruiter_email.lower() in desc or recruiter_email.lower() in summary:
                    return True
            return False
        except Exception as e:
            print(f"Error checking if event is scheduled: {e}")
            return False

    def mark_email_as_read(self, message_id):
        try:
            self.gmail_service.users().messages().batchModify(
                userId='me',
                body={
                    'ids': [message_id],
                    'removeLabelIds': ['UNREAD']
                }
            ).execute()
        except Exception as e:
            print(f"Error marking email as read: {e}")

    def find_event(self, search_query, date_str=None, attendee_email=None):
        from datetime import datetime, timedelta
        local_tz = datetime.now().astimezone().tzinfo
        now = datetime.now(local_tz)
        search_start = now - timedelta(days=2) # Include recent past
        search_end = now + timedelta(days=30)  # Check up to 30 days ahead
        
        try:
            events_result = self.calendar_service.events().list(
                calendarId='primary',
                timeMin=search_start.isoformat(),
                timeMax=search_end.isoformat(),
                singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
        except Exception as e:
            print(f"Error listing events for search: {e}")
            return None
        
        q = search_query.lower()
        for event in events:
            if event.get('status') == 'cancelled':
                continue
            summary = event.get('summary', '').lower()
            description = event.get('description', '').lower()
            attendees = [a.get('email', '').lower() for a in event.get('attendees', []) if a.get('email')]
            
            matches = False
            if q in summary or q in description:
                matches = True
            elif attendee_email and attendee_email.lower() in attendees:
                matches = True
            elif any(q in a for a in attendees):
                matches = True
                
            if date_str and matches:
                evt_start_str = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
                if date_str not in evt_start_str:
                    matches = False
                    
            if matches:
                return event
        return None

    def cancel_calendar_event(self, search_query, start_date=None, attendee_email=None):
        event = self.find_event(search_query, start_date, attendee_email)
        if not event:
            return {"error": "No matching event found to cancel."}
            
        try:
            self.calendar_service.events().delete(
                calendarId='primary',
                eventId=event['id']
            ).execute()
            
            attendees = [a.get('email') for a in event.get('attendees', []) if a.get('email')]
            
            return {
                "success": True,
                "event_summary": event.get('summary'),
                "event_start": event.get('start', {}).get('dateTime') or event.get('start', {}).get('date'),
                "attendees": attendees
            }
        except HttpError as error:
            return {"error": str(error)}

    def reschedule_calendar_event(self, search_query, new_start_time, duration_minutes=None, start_date=None, attendee_email=None):
        event = self.find_event(search_query, start_date, attendee_email)
        if not event:
            return {"error": "No matching event found to reschedule."}
            
        if not duration_minutes:
            from datetime import datetime
            evt_start_str = event.get('start', {}).get('dateTime')
            evt_end_str = event.get('end', {}).get('dateTime')
            if evt_start_str and evt_end_str:
                evt_start = datetime.fromisoformat(evt_start_str.replace('Z', '+00:00'))
                evt_end = datetime.fromisoformat(evt_end_str.replace('Z', '+00:00'))
                duration_minutes = int((evt_end - evt_start).total_seconds() / 60)
            else:
                duration_minutes = 30
                
        # Verify conflicts ignoring the current event
        check_result = self.create_calendar_event(
            summary=event.get('summary'),
            start_time=new_start_time,
            duration_minutes=duration_minutes,
            attendee_email=attendee_email or (event.get('attendees', [{}])[0].get('email') if event.get('attendees') else None),
            description=event.get('description', ''),
            ignore_event_id=event['id']
        )
        
        if isinstance(check_result, dict) and check_result.get('conflict'):
            check_result['original_params']['reschedule_event_id'] = event['id']
            return check_result
            
        # Update the event
        return self.patch_calendar_event_direct(
            event_id=event['id'],
            start_time=new_start_time,
            duration_minutes=duration_minutes,
            summary=event.get('summary'),
            description=event.get('description')
        )

    def patch_calendar_event_direct(self, event_id, start_time, duration_minutes, summary=None, description=None):
        from datetime import datetime, timedelta
        start = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
        end = start + timedelta(minutes=duration_minutes)
        
        body = {
            'start': {
                'dateTime': start.isoformat(),
                'timeZone': 'UTC',
            },
            'end': {
                'dateTime': end.isoformat(),
                'timeZone': 'UTC',
            }
        }
        if summary:
            body['summary'] = summary
        if description:
            body['description'] = description
            
        try:
            event = self.calendar_service.events().patch(
                calendarId='primary',
                eventId=event_id,
                body=body
            ).execute()
            
            return {
                'id': event.get('id'),
                'htmlLink': event.get('htmlLink'),
                'meetLink': event.get('hangoutLink'),
                'summary': event.get('summary'),
                'attendees': [a.get('email') for a in event.get('attendees', []) if a.get('email')]
            }
        except HttpError as error:
            return {'error': str(error)}

    def send_confirmation_email(self, to_email, subject, body_text, attachments=None):
        """
        Sends an email with optional attachments.
        attachments: list of file paths
        """
        try:
            if not attachments:
                message = EmailMessage()
                message.set_content(body_text)
                message['To'] = to_email
                message['Subject'] = subject
                encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            else:
                message = MIMEMultipart()
                message['To'] = to_email
                message['Subject'] = subject
                message.attach(MIMEText(body_text, 'plain'))

                for file_path in attachments:
                    if not os.path.exists(file_path):
                        continue
                    filename = os.path.basename(file_path)
                    with open(file_path, "rb") as attachment:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(attachment.read())
                        encoders.encode_base64(part)
                        part.add_header(
                            "Content-Disposition",
                            f"attachment; filename= {filename}",
                        )
                        message.attach(part)
                encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

            create_message = {'raw': encoded_message}
            send_message = self.gmail_service.users().messages().send(
                userId="me", 
                body=create_message
            ).execute()
            
            return send_message
        except HttpError as error:
            return {'error': str(error)}

    def get_or_create_tracker_sheet(self):
        if self.tracker_spreadsheet_id:
            return self.tracker_spreadsheet_id
            
        from dotenv import load_dotenv
        load_dotenv()
        spreadsheet_id = os.getenv("TRACKER_SPREADSHEET_ID")
        
        if spreadsheet_id:
            return spreadsheet_id
            
        # Create a new spreadsheet
        spreadsheet = {
            'properties': {
                'title': 'Job Application Tracker'
            }
        }
        try:
            spreadsheet = self.sheets_service.spreadsheets().create(body=spreadsheet,
                                        fields='spreadsheetId').execute()
            new_id = spreadsheet.get('spreadsheetId')
            
            # Add header row
            header_values = [
                ["Date", "Recruiter Email", "Position", "Status", "Follow-up Date"]
            ]
            body = {
                'values': header_values
            }
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=new_id, range="Sheet1!A1:E1",
                valueInputOption="RAW", body=body).execute()
                
            # Save to .env
            with open('.env', 'a') as f:
                f.write(f"\nTRACKER_SPREADSHEET_ID={new_id}\n")
                
            return new_id
        except HttpError as error:
            print(f"An error occurred creating sheet: {error}")
            return None

    def log_application(self, spreadsheet_id, date, email, position, status, followup_date):
        if not spreadsheet_id:
            return
            
        values = [
            [date, email, position, status, followup_date]
        ]
        body = {
            'values': values
        }
        try:
            self.sheets_service.spreadsheets().values().append(
                spreadsheetId=spreadsheet_id, range="Sheet1!A:E",
                valueInputOption="USER_ENTERED", insertDataOption="INSERT_ROWS", body=body).execute()
        except HttpError as error:
            print(f"An error occurred logging to sheet: {error}")

    def classify_email(self, subject, snippet, message_id=None):
        import hashlib
        cache_key = message_id if message_id else hashlib.md5(f"{subject}||{snippet}".encode('utf-8')).hexdigest()
        
        # Check cache
        cache = {}
        cache_file = self._get_cache_file()
        if os.path.exists(cache_file):
            try:
                with open(cache_file, 'r') as f:
                    cache = json.load(f)
            except Exception:
                cache = {}
                
        if cache_key in cache:
            if isinstance(cache[cache_key], dict) and "sentiment" in cache[cache_key]:
                return cache[cache_key]["sentiment"]
            return cache[cache_key]

        label = None
        try:
            prompt = (
                "You are an email classifier for a job search tracker platform. "
                "Analyze the following email subject and snippet, and determine the sentiment/category.\n\n"
                f"Subject: {subject}\n"
                f"Snippet: {snippet}\n\n"
                "Classify the email into one of these exact categories:\n"
                "1. 'Rejection' - if it is a rejection email (e.g. not moving forward, thank you for your interest, unfortunate, position filled).\n"
                "2. 'Offer Stage' - if it is a job offer, offer letter, or congrats on selection.\n"
                "3. 'Urgent Action Required' - if the email is about scheduling an interview, booking a time slot, select a slot, calendar invitation, or requires immediate scheduling/meeting action.\n"
                "4. 'Positive Follow-up' - if it is a positive follow-up, coding test, technical assessment, or next steps that are not immediately an interview slot booking/scheduling request.\n"
                "5. 'General Update' - for any other general update or response.\n\n"
                "Respond ONLY with a JSON object in this format:\n"
                '{"label": "CategoryName"}'
            )
            response = self.openai_client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": "You are a precise JSON classifier. Only respond with valid JSON containing the 'label' key."},
                    {"role": "user", "content": prompt}
                ],
                temperature=0.0
            )
            content = response.choices[0].message.content.strip()
            
            # Clean markdown JSON wrapping if present
            if content.startswith("```"):
                lines = content.splitlines()
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines[-1].startswith("```"):
                    lines = lines[:-1]
                content = "\n".join(lines).strip()
                
            data = json.loads(content)
            label = data.get("label")
        except Exception as e:
            print(f"Error classifying email with LLM: {e}. Falling back to keyword classification.")

        if not label:
            # Keyword fallback
            text = f"{subject} {snippet}".lower()
            
            # 1. Rejection
            rejection_keywords = [
                "not moving forward", "unfortunate", "thank you for your interest", "thanks for your interest", 
                "unable to move", "another candidate", "filled the position", "decided to move forward with other", 
                "decided to go with", "not selected", "regret to inform", "unsuccessful", 
                "position has been filled", "rejected", "rejection", "unfortunately", "can't processed",
                "can't proceed", "cannot proceed", "unable to proceed", "decided not to", "no longer considering",
                "will not be moving forward", "not to proceed", "unable to move forward", "not move forward"
            ]
            if any(kw in text for kw in rejection_keywords):
                label = "Rejection"
            
            # 2. Offer Stage
            elif any(kw in text for kw in [
                "offer letter", "congratulations", "job offer", "offering you", "onboard", "onboarding", 
                "compensation details", "selected for the role", "happy to offer"
            ]):
                label = "Offer Stage"
                
            # 3. Urgent Action Required
            elif any(kw in text for kw in [
                "action required", "urgent", "update required", "please reply", "call me", "asap", 
                "scheduling", "schedule an interview", "book a time", "select a slot", "availability", 
                "choose a slot", "book your slot", "pick a time", "schedule your", "needs response", 
                "schedule today"
            ]):
                label = "Urgent Action Required"
                
            # 4. Positive Follow-up
            elif any(kw in text for kw in [
                "shortlisted", "next steps", "interview scheduled", "interview invitation", "technical round", 
                "assessment", "round 1", "round 2", "round 3", "coding test", "interview request", "discuss",
                "conversation", "touch base", "chat about"
            ]):
                label = "Positive Follow-up"
                
            else:
                label = "General Update"

        # Return dict based on final label
        if label == "Rejection":
            res = {
                "label": "Rejection",
                "color": "#ef4444",
                "bg": "rgba(239, 68, 68, 0.15)",
                "border": "rgba(239, 68, 68, 0.3)"
            }
        elif label == "Offer Stage":
            res = {
                "label": "Offer Stage",
                "color": "#10b981",
                "bg": "rgba(16, 185, 129, 0.15)",
                "border": "rgba(16, 185, 129, 0.3)"
            }
        elif label == "Urgent Action Required":
            res = {
                "label": "Urgent Action Required",
                "color": "#f59e0b",
                "bg": "rgba(245, 158, 11, 0.15)",
                "border": "rgba(245, 158, 11, 0.3)",
                "animate": True
            }
        elif label == "Positive Follow-up":
            res = {
                "label": "Positive Follow-up",
                "color": "#3b82f6",
                "bg": "rgba(59, 130, 246, 0.15)",
                "border": "rgba(59, 130, 246, 0.3)"
            }
        else:
            res = {
                "label": "General Update",
                "color": "#94a3b8",
                "bg": "rgba(148, 163, 184, 0.15)",
                "border": "rgba(148, 163, 184, 0.3)"
            }

        # Save to cache
        try:
            if cache_key in cache and isinstance(cache[cache_key], dict) and "sentiment" in cache[cache_key]:
                cache[cache_key]["sentiment"] = res
            else:
                cache[cache_key] = res
            with open(cache_file, 'w') as f:
                json.dump(cache, f, indent=4)
        except Exception as e:
            print(f"Error saving classification cache: {e}")

        return res

    def get_important_emails(self, max_results=15):
        try:
            query = (
                '(subject:(interview OR offer OR scheduling OR schedule OR "next steps" OR congratulations OR shortlisted OR "interview update" OR "interview schedule") '
                'OR "interview schedule" OR "offer letter") '
                '-subject:("application received" OR "thank you for applying" OR "received your application" OR "applied") '
                '-"daily digest" -"weekly digest" -digest -newsletter -alert -"job alert" -"job alerts" -"new jobs" '
                '-"job recommendation" -"recommended jobs" -"top questions" -from:AmbitionBox -from:Glassdoor -from:Indeed '
                '-from:Wellfound -from:LeetCode -from:Medium'
            )
            results = self.gmail_service.users().messages().list(userId='me', q=query, maxResults=max_results).execute()
            messages = results.get('messages', [])

            # Check cache first
            cache = {}
            cache_file = self._get_cache_file()
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, 'r') as f:
                        cache = json.load(f)
                except Exception:
                    cache = {}

            email_data = []
            cache_updated = False
            for msg in messages:
                msg_id = msg['id']
                if msg_id in cache and isinstance(cache[msg_id], dict) and "subject" in cache[msg_id]:
                    # Load from cache
                    email_data.append(cache[msg_id])
                else:
                    msg_detail = self.gmail_service.users().messages().get(userId='me', id=msg_id, format='metadata', metadataHeaders=['Subject', 'From', 'Date']).execute()
                    headers = msg_detail.get('payload', {}).get('headers', [])
                    
                    subject = "No Subject"
                    sender = "Unknown"
                    date = ""
                    
                    for header in headers:
                        name = header.get('name', '').lower()
                        if name == 'subject':
                            subject = header.get('value')
                        elif name == 'from':
                            sender = header.get('value')
                        elif name == 'date':
                            date = header.get('value')
                    
                    # Check for UNREAD label
                    labels = msg_detail.get('labelIds', [])
                    is_unread = 'UNREAD' in labels
                    snippet = msg_detail.get('snippet', '')
                    sentiment = self.classify_email(subject, snippet, message_id=msg_id)
                    
                    email_entry = {
                        'id': msg_id,
                        'subject': subject,
                        'from': sender,
                        'date': date,
                        'snippet': snippet,
                        'unread': is_unread,
                        'sentiment': sentiment
                    }
                    email_data.append(email_entry)
                    cache[msg_id] = email_entry
                    cache_updated = True
                    
            if cache_updated:
                try:
                    with open(cache_file, 'w') as f:
                        json.dump(cache, f, indent=4)
                except Exception as e:
                    print(f"Error saving updated cache: {e}")
                    
            return email_data
        except HttpError as error:
            print(f"An error occurred fetching emails: {error}")
            return []

    def get_email_detail(self, message_id):
        try:
            # Mark email as read by removing UNREAD label
            try:
                self.gmail_service.users().messages().batchModify(
                    userId='me',
                    body={
                        'ids': [message_id],
                        'removeLabelIds': ['UNREAD']
                    }
                ).execute()
                
                # Update local cache to mark as read
                cache_file = self._get_cache_file()
                if os.path.exists(cache_file):
                    try:
                        with open(cache_file, 'r') as f:
                            cache = json.load(f)
                        if message_id in cache and isinstance(cache[message_id], dict):
                            cache[message_id]['unread'] = False
                            with open(cache_file, 'w') as f:
                                json.dump(cache, f, indent=4)
                    except Exception as ce:
                        print(f"Error updating cache on read: {ce}")
            except Exception as e:
                print(f"Error marking email as read: {e}")

            msg = self.gmail_service.users().messages().get(userId='me', id=message_id, format='full').execute()
            payload = msg.get('payload', {})
            headers = payload.get('headers', [])
            
            subject = "No Subject"
            sender = "Unknown"
            date = ""
            for header in headers:
                name = header.get('name', '').lower()
                if name == 'subject':
                    subject = header.get('value')
                elif name == 'from':
                    sender = header.get('value')
                elif name == 'date':
                    date = header.get('value')
            
            body = ""
            
            def get_body_from_parts(parts):
                html_body = ""
                text_body = ""
                for part in parts:
                    mime_type = part.get('mimeType')
                    body_data = part.get('body', {}).get('data')
                    
                    if mime_type == 'text/html' and body_data:
                        html_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                    elif mime_type == 'text/plain' and body_data:
                        text_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                    elif 'parts' in part:
                        # Recursive call for nested parts
                        nested_html, nested_text = get_body_from_parts(part['parts'])
                        if nested_html:
                            html_body = nested_html
                        if nested_text and not text_body:
                            text_body = nested_text
                return html_body, text_body

            if 'parts' in payload:
                html, text = get_body_from_parts(payload['parts'])
                body = html if html else text
            else:
                body_data = payload.get('body', {}).get('data')
                if body_data:
                    body = base64.urlsafe_b64decode(body_data).decode('utf-8')
            
            return {
                'id': message_id,
                'subject': subject,
                'from': sender,
                'date': date,
                'body': body
            }
        except HttpError as error:
            print(f"An error occurred getting email detail: {error}")
            return None

    def get_applications(self, spreadsheet_id):
        try:
            result = self.sheets_service.spreadsheets().values().get(
                spreadsheetId=spreadsheet_id, range="Sheet1!A:E"
            ).execute()
            return result.get('values', [])
        except Exception as e:
            print(f"Error reading spreadsheet: {e}")
            return []

    def update_application_status(self, spreadsheet_id, row_number, status):
        """
        Updates the Status column (column D) in a specific row.
        row_number is 1-indexed (e.g. 2 for the first data row).
        """
        try:
            body = {
                'values': [[status]]
            }
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range=f"Sheet1!D{row_number}",
                valueInputOption="RAW", body=body
            ).execute()
            return True
        except Exception as e:
            print(f"Error updating spreadsheet row {row_number}: {e}")
            return False

    def has_recruiter_replied(self, recruiter_email, since_datetime):
        """
        Checks if we received any email from recruiter_email since since_datetime.
        since_datetime: datetime object
        """
        try:
            date_str = since_datetime.strftime("%Y/%m/%d")
            query = f"from:{recruiter_email} after:{date_str}"
            results = self.gmail_service.users().messages().list(userId='me', q=query).execute()
            messages = results.get('messages', [])
            
            for msg in messages:
                msg_detail = self.gmail_service.users().messages().get(
                    userId='me', id=msg['id'], format='metadata', metadataHeaders=['Date']
                ).execute()
                headers = msg_detail.get('payload', {}).get('headers', [])
                for h in headers:
                    if h.get('name', '').lower() == 'date':
                        from email.utils import parsedate_to_datetime
                        msg_date = parsedate_to_datetime(h.get('value'))
                        if since_datetime.tzinfo is None and msg_date.tzinfo is not None:
                            msg_date = msg_date.replace(tzinfo=None)
                        if msg_date > since_datetime:
                            return True
            return False
        except Exception as e:
            print(f"Error checking replies from {recruiter_email}: {e}")
            return False

    def get_last_sent_message(self, recruiter_email, position=None):
        """
        Finds the last message we sent to recruiter_email.
        Returns (thread_id, message_id, subject) or (None, None, None)
        """
        try:
            query = f"to:{recruiter_email}"
            results = self.gmail_service.users().messages().list(userId='me', q=query, maxResults=15).execute()
            messages = results.get('messages', [])
            
            for msg in messages:
                msg_detail = self.gmail_service.users().messages().get(
                    userId='me', id=msg['id'], format='metadata', 
                    metadataHeaders=['Message-ID', 'Subject']
                ).execute()
                
                headers = msg_detail.get('payload', {}).get('headers', [])
                msg_id = None
                subject = ""
                
                for h in headers:
                    name = h.get('name', '').lower()
                    if name == 'message-id':
                        msg_id = h.get('value')
                    elif name == 'subject':
                        subject = h.get('value')
                
                if msg_id:
                    subject_lower = subject.lower()
                    is_app_email = "application" in subject_lower or "apply" in subject_lower or "applying" in subject_lower
                    if position and position.lower() in subject_lower:
                        is_app_email = True
                    
                    if is_app_email:
                        return msg['threadId'], msg_id, subject
            
            # Fallback to the absolute first message if no specific application subject is found
            if messages:
                msg = messages[0]
                msg_detail = self.gmail_service.users().messages().get(
                    userId='me', id=msg['id'], format='metadata', 
                    metadataHeaders=['Message-ID', 'Subject']
                ).execute()
                headers = msg_detail.get('payload', {}).get('headers', [])
                msg_id = None
                subject = "Follow-up"
                for h in headers:
                    name = h.get('name', '').lower()
                    if name == 'message-id':
                        msg_id = h.get('value')
                    elif name == 'subject':
                        subject = h.get('value')
                if msg_id:
                    return msg['threadId'], msg_id, subject

            return None, None, None
        except Exception as e:
            print(f"Error getting last sent message to {recruiter_email}: {e}")
            return None, None, None

    def send_threaded_reply(self, to_email, subject, body_text, thread_id, original_message_id):
        """
        Sends an email that threads under thread_id.
        """
        try:
            message = EmailMessage()
            message.set_content(body_text)
            message['To'] = to_email
            
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
            message['Subject'] = subject
            
            message['In-Reply-To'] = original_message_id
            message['References'] = original_message_id
            
            encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            create_message = {
                'raw': encoded_message,
                'threadId': thread_id
            }
            
            send_message = self.gmail_service.users().messages().send(
                userId="me", 
                body=create_message
            ).execute()
            return send_message
        except Exception as e:
            print(f"Error sending threaded reply to {to_email}: {e}")
            return {'error': str(e)}

    def get_recruiter_reply(self, recruiter_email, since_datetime):
        """
        Finds the last message we received from recruiter_email since since_datetime.
        Returns (thread_id, msg_id, subject, body_text) or None
        """
        try:
            date_str = since_datetime.strftime("%Y/%m/%d")
            query = f"from:{recruiter_email} after:{date_str}"
            results = self.gmail_service.users().messages().list(userId='me', q=query).execute()
            messages = results.get('messages', [])
            
            for msg in messages:
                msg_detail = self.gmail_service.users().messages().get(
                    userId='me', id=msg['id'], format='full'
                ).execute()
                
                payload = msg_detail.get('payload', {})
                headers = payload.get('headers', [])
                
                from email.utils import parsedate_to_datetime
                msg_date = None
                subject = "Reply"
                msg_id = None
                
                for h in headers:
                    name = h.get('name', '').lower()
                    if name == 'date':
                        msg_date = parsedate_to_datetime(h.get('value'))
                    elif name == 'subject':
                        subject = h.get('value')
                    elif name == 'message-id':
                        msg_id = h.get('value')
                
                if msg_date:
                    if since_datetime.tzinfo is None and msg_date.tzinfo is not None:
                        msg_date = msg_date.replace(tzinfo=None)
                    if msg_date > since_datetime:
                        # Fetch body
                        body = ""
                        def get_body_from_parts(parts):
                            html_body = ""
                            text_body = ""
                            for part in parts:
                                mime_type = part.get('mimeType')
                                body_data = part.get('body', {}).get('data')
                                
                                if mime_type == 'text/html' and body_data:
                                    html_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                                elif mime_type == 'text/plain' and body_data:
                                    text_body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                                elif 'parts' in part:
                                    nested_html, nested_text = get_body_from_parts(part['parts'])
                                    if nested_html:
                                        html_body = nested_html
                                    if nested_text and not text_body:
                                        text_body = nested_text
                            return html_body, text_body

                        if 'parts' in payload:
                            html, text = get_body_from_parts(payload['parts'])
                            body = html if html else text
                        else:
                            body_data = payload.get('body', {}).get('data')
                            if body_data:
                                body = base64.urlsafe_b64decode(body_data).decode('utf-8')
                        
                        is_unread = 'UNREAD' in msg_detail.get('labelIds', [])
                        return msg['threadId'], msg['id'], msg_id, subject, body, is_unread
            return None
        except Exception as e:
            print(f"Error getting recruiter reply from {recruiter_email}: {e}")
            return None

    def get_scheduled_interviews(self):
        """
        Lists upcoming calendar events containing interview keywords.
        Returns a list of dictionaries with event details.
        """
        from datetime import datetime
        try:
            now = datetime.utcnow().isoformat() + 'Z'
            events_result = self.calendar_service.events().list(
                calendarId='primary', timeMin=now, maxResults=50, singleEvents=True,
                orderBy='startTime'
            ).execute()
            events = events_result.get('items', [])
            
            interviews = []
            interview_keywords = ["interview", "meeting", "sync", "discussion", "chat", "call", "google meet"]
            for event in events:
                summary = event.get('summary', '').lower()
                description = event.get('description', '').lower()
                
                # Check if it matches interview keywords
                is_interview = any(keyword in summary or keyword in description for keyword in interview_keywords)
                if is_interview:
                    start = event.get('start', {}).get('dateTime') or event.get('start', {}).get('date')
                    end = event.get('end', {}).get('dateTime') or event.get('end', {}).get('date')
                    meet_link = event.get('location') or event.get('hangoutLink') or ''
                    if not meet_link:
                        import re
                        urls = re.findall(r'https?://[^\s]+', event.get('description', ''))
                        if urls:
                            meet_link = urls[0].split('<')[0].split('"')[0] # Clean any HTML tags
                    
                    interviews.append({
                        "id": event.get('id'),
                        "summary": event.get('summary'),
                        "start": start,
                        "end": end,
                        "description": event.get('description', ''),
                        "meetLink": meet_link
                    })
            return interviews
        except Exception as e:
            print(f"Error listing calendar events: {e}")
            return []

    def update_application_status_with_format(self, spreadsheet_id, row_number, status, color=None):
        """
        Updates the Status column (column D) in a specific row.
        If color is 'red', applies a light red background and dark red bold text.
        Otherwise clears custom formatting.
        """
        try:
            body = {
                'values': [[status]]
            }
            self.sheets_service.spreadsheets().values().update(
                spreadsheetId=spreadsheet_id, range=f"Sheet1!D{row_number}",
                valueInputOption="USER_ENTERED", body=body
            ).execute()
            
            # Now, apply formatting if color is specified
            sheet_metadata = self.sheets_service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
            sheets = sheet_metadata.get('sheets', [])
            sheet_id = 0
            for s in sheets:
                if s.get('properties', {}).get('title') == 'Sheet1':
                    sheet_id = s.get('properties', {}).get('sheetId', 0)
                    break
            
            requests = []
            if color == 'red':
                requests.append({
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": 3, # Column D
                            "endColumnIndex": 4
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": status},
                                        "userEnteredFormat": {
                                            "backgroundColor": {
                                                "red": 254/255.0,
                                                "green": 226/255.0,
                                                "blue": 226/255.0
                                            },
                                            "textFormat": {
                                                "foregroundColor": {
                                                    "red": 220/255.0,
                                                    "green": 38/255.0,
                                                    "blue": 38/255.0
                                                },
                                                "bold": True
                                            }
                                        }
                                    }
                                ]
                            }
                        ],
                        "fields": "userEnteredValue,userEnteredFormat(backgroundColor,textFormat)"
                    }
                })
            else:
                requests.append({
                    "updateCells": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": row_number - 1,
                            "endRowIndex": row_number,
                            "startColumnIndex": 3,
                            "endColumnIndex": 4
                        },
                        "rows": [
                            {
                                "values": [
                                    {
                                        "userEnteredValue": {"stringValue": status},
                                        "userEnteredFormat": {}
                                    }
                                ]
                            }
                        ],
                        "fields": "userEnteredValue,userEnteredFormat"
                    }
                })
                
            if requests:
                self.sheets_service.spreadsheets().batchUpdate(
                    spreadsheetId=spreadsheet_id, body={"requests": requests}
                ).execute()
            return True
        except Exception as e:
            print(f"Error updating spreadsheet format for row {row_number}: {e}")
            return False
