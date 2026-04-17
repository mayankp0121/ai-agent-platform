import os
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

# If modifying these scopes, delete the file token.json.
SCOPES = [
    'https://www.googleapis.com/auth/calendar.events',
    'https://www.googleapis.com/auth/gmail.send'
]

class GoogleServices:
    def __init__(self, credentials_path='credentials.json', token_path='token.json'):
        self.credentials_path = credentials_path
        self.token_path = token_path
        self.creds = self._authenticate()
        self.calendar_service = build('calendar', 'v3', credentials=self.creds)
        self.gmail_service = build('gmail', 'v1', credentials=self.creds)

    def _authenticate(self):
        creds = None
        if os.path.exists(self.token_path):
            creds = Credentials.from_authorized_user_file(self.token_path, SCOPES)
        
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists(self.credentials_path):
                    raise FileNotFoundError(f"{self.credentials_path} not found. Please provide it from Google Cloud Console.")
                flow = InstalledAppFlow.from_client_secrets_file(self.credentials_path, SCOPES)
                creds = flow.run_local_server(port=0)
            
            with open(self.token_path, 'w') as token:
                token.write(creds.to_json())
        return creds

    def create_calendar_event(self, summary, start_time, duration_minutes, attendee_email, description=""):
        """
        Creates a calendar event and returns the GMeet link.
        """
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
