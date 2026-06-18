import os
import json
from flask import Flask, render_template, request, jsonify, session
from flask_cors import CORS
from google_services import GoogleServices
from scheduler_agent import SchedulerAgent
from job_agent import JobAgent
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "antigravity-secret-key-987654321")
CORS(app)

user_services = {}

def get_services():
    email = session.get('user_email')
    if not email:
        raise Exception("Unauthorized: Please log in first.")
        
    if email in user_services:
        return user_services[email]
        
    if not os.path.exists('users.json'):
        raise Exception("No registered users found. Please sign up.")
        
    with open('users.json', 'r') as f:
        users = json.load(f)
        
    if email not in users:
        raise Exception("Session user not found. Please log in again.")
        
    user_data = users[email]
    token_data = user_data.get('token_data')
    spreadsheet_id = user_data.get('tracker_spreadsheet_id')
    
    gs = GoogleServices(token_data=token_data, tracker_spreadsheet_id=spreadsheet_id, user_email=email)
    sa = SchedulerAgent(gs)
    ja = JobAgent(gs)
    
    user_services[email] = (gs, sa, ja)
    return gs, sa, ja

@app.before_request
def check_login():
    allowed_routes = ['index', 'login', 'signup', 'auth_status', 'static']
    if request.endpoint in allowed_routes or (request.path and request.path.startswith('/static/')):
        return
    if not request.endpoint:
        return
        
    if 'user_email' not in session:
        return jsonify({"error": "Unauthorized: Please log in first."}), 401

@app.route('/api/auth-status', methods=['GET'])
def auth_status():
    email = session.get('user_email')
    if not email:
        return jsonify({"authenticated": False})
        
    try:
        if os.path.exists('users.json'):
            with open('users.json', 'r') as f:
                users = json.load(f)
            if email in users:
                return jsonify({
                    "authenticated": True,
                    "email": email,
                    "tracker_spreadsheet_id": users[email].get('tracker_spreadsheet_id')
                })
    except Exception as e:
        print(f"Error checking auth status: {e}")
        
    return jsonify({"authenticated": False})

@app.route('/api/signup', methods=['POST'])
def signup():
    try:
        data = request.json
        email = data.get('email')
        spreadsheet_id = data.get('tracker_spreadsheet_id')
        token_data = data.get('token_data')
        
        if not email or not spreadsheet_id or not token_data:
            return jsonify({"error": "Email, Spreadsheet ID, and token_data are required."}), 400
            
        email = email.strip().lower()
        
        users = {}
        if os.path.exists('users.json'):
            try:
                with open('users.json', 'r') as f:
                    users = json.load(f)
            except Exception:
                users = {}
                
        users[email] = {
            "email": email,
            "tracker_spreadsheet_id": spreadsheet_id.strip(),
            "token_data": token_data
        }
        
        with open('users.json', 'w') as f:
            json.dump(users, f, indent=4)
            
        session['user_email'] = email
        
        if email in user_services:
            del user_services[email]
            
        return jsonify({"success": True, "email": email})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/signup-bulk', methods=['POST'])
def signup_bulk():
    try:
        data = request.json
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Invalid data format. Expected dictionary of users."}), 400
            
        users = {}
        if os.path.exists('users.json'):
            try:
                with open('users.json', 'r') as f:
                    users = json.load(f)
            except Exception:
                users = {}
                
        imported_emails = []
        for email, user_info in data.items():
            if not isinstance(user_info, dict):
                continue
            u_email = user_info.get('email')
            u_sheet = user_info.get('tracker_spreadsheet_id')
            u_token = user_info.get('token_data')
            
            if u_email and u_sheet and u_token:
                clean_email = u_email.strip().lower()
                users[clean_email] = {
                    "email": clean_email,
                    "tracker_spreadsheet_id": u_sheet.strip(),
                    "token_data": u_token
                }
                imported_emails.append(clean_email)
                
        if not imported_emails:
            return jsonify({"error": "No valid user profiles found to import."}), 400
            
        with open('users.json', 'w') as f:
            json.dump(users, f, indent=4)
            
        session['user_email'] = imported_emails[0]
        
        for email in imported_emails:
            if email in user_services:
                del user_services[email]
                
        return jsonify({"success": True, "imported": imported_emails, "email": imported_emails[0]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/login', methods=['POST'])
def login():
    try:
        data = request.json
        email = data.get('email')
        
        if not email:
            return jsonify({"error": "Email is required."}), 400
            
        email = email.strip().lower()
        
        if not os.path.exists('users.json'):
            return jsonify({"error": "No users registered yet. Please sign up first."}), 404
            
        with open('users.json', 'r') as f:
            users = json.load(f)
            
        if email not in users:
            return jsonify({"error": "Email not registered. Please sign up first."}), 404
            
        session['user_email'] = email
        
        if email in user_services:
            del user_services[email]
            
        return jsonify({
            "success": True, 
            "email": email,
            "tracker_spreadsheet_id": users[email].get('tracker_spreadsheet_id')
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/logout', methods=['POST'])
def logout():
    session.pop('user_email', None)
    return jsonify({"success": True})

@app.route('/api/export-profile', methods=['GET'])
def export_profile():
    email = session.get('user_email')
    if not email:
        return jsonify({"error": "Unauthorized"}), 401
        
    if not os.path.exists('users.json'):
        return jsonify({"error": "User not found"}), 404
        
    with open('users.json', 'r') as f:
        users = json.load(f)
        
    if email not in users:
        return jsonify({"error": "User not found"}), 404
        
    user_data = users[email]
    response_data = {
        "email": user_data.get('email'),
        "tracker_spreadsheet_id": user_data.get('tracker_spreadsheet_id'),
        "token_data": user_data.get('token_data')
    }
    return jsonify(response_data)

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/schedule', methods=['POST'])
def schedule():
    data = request.json
    prompt = data.get('prompt')
    
    if not prompt:
        return jsonify({"error": "No prompt provided"}), 400
    
    try:
        _, agent, _ = get_services()
        response = agent.process_prompt(prompt)
        if isinstance(response, dict) and response.get("conflict"):
            return jsonify(response)
        return jsonify({"response": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/suggest-prompts', methods=['GET'])
def suggest_prompts():
    try:
        _, agent, _ = get_services()
        
        prompt = (
            "Generate exactly 2 diverse and natural-sounding sample prompt suggestions for a user to type into an AI meeting scheduler assistant.\n"
            "The assistant has the ability to:\n"
            "1. Schedule new meetings/interviews/syncs (e.g. 'Schedule a 30m sync with hr@company.com tomorrow at 10 AM')\n"
            "2. Reschedule existing calendar events (e.g. 'Reschedule my tech interview with Google to Friday at 3 PM')\n"
            "3. Cancel meetings (e.g. 'Cancel my meeting with recruiter@uber.com scheduled for Friday')\n"
            "4. Schedule personal Focus Blocks / blocks (e.g. 'Schedule a 2 hour Focus Block for today at 4 PM')\n\n"
            "The current year is 2026.\n"
            "Return ONLY a JSON array of strings containing the 2 prompts. Ensure they are diverse (e.g. one for scheduling, one for rescheduling or cancelling, or one focus block). Do not wrap in markdown or backticks.\n"
            "Example response:\n"
            '["Schedule a 45m technical interview with recruiter@stripe.com tomorrow at 2 PM", "Reschedule my sync with John on Friday to next Monday at 11 AM"]'
        )
        
        response = agent.client.chat.completions.create(
            model=agent.model,
            messages=[
                {"role": "system", "content": "You are a precise JSON generator. Return ONLY a valid JSON array of 2 strings."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.85
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
            
        prompts = json.loads(content)
        # fallback just in case JSON parsing fails or has incorrect length
        if not isinstance(prompts, list) or len(prompts) < 2:
            prompts = [
                "Schedule a 30m technical interview with HR tomorrow at 2 PM",
                "Reschedule my sync with John on Friday to next Monday at 11 AM"
            ]
        return jsonify({"prompts": prompts[:2]})
    except Exception as e:
        print(f"Error generating suggest prompts: {e}")
        # Default fallback prompts
        import random
        defaults = [
            ["Schedule a 30m sync with recruiter@amazon.com tomorrow at 10 AM", "Reschedule my interview with Google to next Monday at 2 PM"],
            ["Schedule a 1 hour technical round with Aditya on Friday at 4 PM", "Cancel my sync scheduled for tomorrow morning"],
            ["Schedule a 2 hour Focus Block for today at 3 PM", "Reschedule my coding interview with Stripe to next Thursday at 3:30 PM"],
            ["Schedule a 45m interview with hr@microsoft.com tomorrow at 11:30 AM", "Reschedule my mock interview on Wednesday to Thursday at 1 PM"]
        ]
        return jsonify({"prompts": random.choice(defaults)})

@app.route('/api/confirm-schedule', methods=['POST'])
def confirm_schedule():
    data = request.json
    summary = data.get('summary')
    start_time = data.get('start_time')
    duration_minutes = int(data.get('duration_minutes', 30))
    attendee_email = data.get('attendee_email')
    description = data.get('description', '')
    reschedule_event_id = data.get('reschedule_event_id')

    if not reschedule_event_id and not all([summary, start_time, attendee_email]):
        return jsonify({"error": "Missing required fields"}), 400

    try:
        google_services, _, _ = get_services()
        if reschedule_event_id:
            result = google_services.patch_calendar_event_direct(
                event_id=reschedule_event_id,
                start_time=start_time,
                duration_minutes=duration_minutes,
                summary=summary,
                description=description
            )
            if not attendee_email and isinstance(result, dict) and result.get('attendees'):
                attendee_email = result['attendees'][0]
        else:
            result = google_services.create_calendar_event_direct(
                summary=summary,
                start_time=start_time,
                duration_minutes=duration_minutes,
                attendee_email=attendee_email,
                description=description
            )
        
        if 'error' in result:
            return jsonify({"error": result['error']}), 500

        meet_link = result.get('meetLink', '')
        
        try:
            from datetime import datetime
            dt = datetime.fromisoformat(start_time.replace('Z', '+00:00'))
            date_str = dt.strftime('%A, %B %d, %Y at %I:%M %p')
        except Exception:
            date_str = start_time
            
        action_verb = "rescheduled" if reschedule_event_id else "scheduled"
        subject = f"Rescheduled: {summary}" if reschedule_event_id else f"Confirmed: {summary}"
        body_text = (
            f"Hello,\n\n"
            f"Your meeting has been {action_verb} successfully.\n\n"
            f"Meeting Details:\n"
            f"- Title: {summary}\n"
            f"- Time: {date_str}\n"
            f"- Google Meet Link: {meet_link}\n\n"
            f"See you then!\n\n"
            f"Best regards,\n"
            f"The Team"
        )
        
        if attendee_email:
            google_services.send_confirmation_email(
                to_email=attendee_email,
                subject=subject,
                body_text=body_text
            )
        
        return jsonify({
            "response": f"Meeting {action_verb} successfully for {date_str}! A confirmation email with the Google Meet link ({meet_link}) has been sent."
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/job-apply', methods=['POST'])
def job_apply():
    # Use request.form instead of request.json for multipart
    emails = request.form.get('emails')
    position = request.form.get('position')
    mode = request.form.get('mode', 'generate')
    custom_letter = request.form.get('custom_letter', '')
    
    # Handle Attachments (Resume and optional CSV)
    attachment_path = None
    csv_path = None
    
    if 'resume' in request.files:
        res_file = request.files['resume']
        if res_file.filename != '':
            upload_dir = 'uploads'
            if not os.path.exists(upload_dir): os.makedirs(upload_dir)
            attachment_path = os.path.join(upload_dir, res_file.filename)
            res_file.save(attachment_path)

    if 'csv_file' in request.files:
        csv_file = request.files['csv_file']
        if csv_file.filename != '':
            upload_dir = 'uploads'
            if not os.path.exists(upload_dir): os.makedirs(upload_dir)
            csv_path = os.path.join(upload_dir, csv_file.filename)
            csv_file.save(csv_path)

    try:
        _, _, job_agent = get_services()
        if csv_path:
            response = job_agent.process_csv_applications(csv_path, mode, custom_letter, attachment_path)
        else:
            if not emails or not position:
                return jsonify({"error": "Emails and position are required for manual mode"}), 400
            response = job_agent.process_bulk_applications(emails, position, mode, custom_letter, attachment_path)
        
        return jsonify({"response": response})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/tracker-url', methods=['GET'])
def get_tracker_url():
    try:
        _, _, job_agent = get_services()
        if job_agent.spreadsheet_id:
            return jsonify({"url": f"https://docs.google.com/spreadsheets/d/{job_agent.spreadsheet_id}/edit"})
        return jsonify({"error": "Tracker sheet not configured"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/sync-followup', methods=['POST'])
def sync_followup():
    try:
        _, _, job_agent = get_services()
        result = job_agent.sync_and_followup()
        return jsonify(result)
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/imp-mails', methods=['GET'])
def get_imp_mails():
    try:
        google_services, _, _ = get_services()
        emails = google_services.get_important_emails()
        return jsonify({"emails": emails})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/mail-detail/<mail_id>', methods=['GET'])
def get_mail_detail(mail_id):
    try:
        google_services, _, _ = get_services()
        email_detail = google_services.get_email_detail(mail_id)
        if email_detail:
            return jsonify(email_detail)
        return jsonify({"error": "Email not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/pipeline-data', methods=['GET'])
def get_pipeline_data():
    try:
        from datetime import datetime
        google_services, _, job_agent = get_services()
        
        # 1. Fetch tracker sheet applications
        rows = google_services.get_applications(job_agent.spreadsheet_id)
        if not rows or len(rows) <= 1:
            # Empty spreadsheet or only headers
            return jsonify({
                "outbound_sent": 0,
                "applied_count": 0,
                "followup_count": 0,
                "response_count": 0,
                "response_rate": 0,
                "interviews_count": 0,
                "applications": [],
                "interviews": [],
                "sentiment_breakdown": {
                    "Warm/Positive": 0,
                    "Neutral/General": 0,
                    "Rejection": 0
                }
            })
        
        headers = rows[0]
        try:
            date_idx = headers.index("Date")
            email_idx = headers.index("Recruiter Email")
            pos_idx = headers.index("Position")
            status_idx = headers.index("Status")
        except ValueError:
            date_idx, email_idx, pos_idx, status_idx = 0, 1, 2, 3

        applications = []
        outbound_sent = len(rows) - 1
        applied_count = 0
        followup_count = 0
        response_count = 0
        sentiment_breakdown = {
            "Warm/Positive": 0,
            "Neutral/General": 0,
            "Rejection": 0
        }

        for i, row in enumerate(rows[1:], start=2):
            while len(row) < len(headers):
                row.append("")
                
            app_date_str = row[date_idx]
            recruiter_email = row[email_idx]
            position = row[pos_idx]
            status = row[status_idx]
            
            status_clean = status.strip().lower()
            if status_clean == "applied":
                applied_count += 1
            elif status_clean.startswith("followed up"):
                followup_count += 1
            elif status_clean in ["replied", "replied & answered", "priority check", "interview scheduled", "interview proposed"]:
                response_count += 1

            sentiment_obj = None
            reply_subject = ""
            reply_body = ""
            if status_clean in ["replied", "replied & answered", "priority check", "interview scheduled", "interview proposed"]:
                try:
                    if " " in app_date_str:
                        app_dt = datetime.strptime(app_date_str, "%Y-%m-%d %H:%M:%S")
                    else:
                        app_dt = datetime.strptime(app_date_str, "%Y-%m-%d")
                except Exception:
                    app_dt = datetime.now()
                
                reply_info = google_services.get_recruiter_reply(recruiter_email, app_dt)
                if reply_info:
                    _, reply_msg_id, _, reply_subject, reply_body, _ = reply_info
                    snippet = reply_body[:150] if reply_body else ""
                    sentiment_obj = google_services.classify_email(reply_subject, snippet, message_id=reply_msg_id)
                
                if not sentiment_obj:
                    if status_clean in ["interview scheduled", "interview proposed"]:
                        sentiment_obj = {
                            "label": "Positive Follow-up",
                            "color": "#10b981",
                            "bg": "rgba(16, 185, 129, 0.15)",
                            "border": "rgba(16, 185, 129, 0.3)"
                        }
                    elif status_clean == "priority check":
                        sentiment_obj = {
                            "label": "Urgent Action Required",
                            "color": "#f59e0b",
                            "bg": "rgba(245, 158, 11, 0.15)",
                            "border": "rgba(245, 158, 11, 0.3)",
                            "animate": True
                        }
                    else:
                        sentiment_obj = {
                            "label": "General Update",
                            "color": "#94a3b8",
                            "bg": "rgba(148, 163, 184, 0.15)",
                            "border": "rgba(148, 163, 184, 0.3)"
                        }

                lbl = sentiment_obj.get("label", "")
                if lbl in ["Positive Follow-up", "Offer Stage", "Urgent Action Required"]:
                    sentiment_breakdown["Warm/Positive"] += 1
                elif lbl == "Rejection":
                    sentiment_breakdown["Rejection"] += 1
                else:
                    sentiment_breakdown["Neutral/General"] += 1

            applications.append({
                "row_index": i,
                "date": app_date_str,
                "email": recruiter_email,
                "position": position,
                "status": status,
                "sentiment": sentiment_obj,
                "reply_subject": reply_subject,
                "reply_body": reply_body
            })

        response_rate = round((response_count / outbound_sent) * 100, 1) if outbound_sent > 0 else 0

        interviews = google_services.get_scheduled_interviews()
        interviews_count = len(interviews)

        return jsonify({
            "outbound_sent": outbound_sent,
            "applied_count": applied_count,
            "followup_count": followup_count,
            "response_count": response_count,
            "response_rate": response_rate,
            "interviews_count": interviews_count,
            "applications": applications,
            "interviews": interviews,
            "sentiment_breakdown": sentiment_breakdown
        })
    except Exception as e:
        print(f"Error fetching pipeline data: {e}")
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    # Ensure templates directory exists
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    print("Starting Web Interface on http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
