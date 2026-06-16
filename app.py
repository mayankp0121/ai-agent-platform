import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from google_services import GoogleServices
from scheduler_agent import SchedulerAgent
from job_agent import JobAgent
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# Initialize Google Services and Agent
# Note: This will trigger the OAuth flow on the first request if credentials aren't already valid
google_services = None
agent = None
job_agent = None

def get_services():
    global google_services, agent, job_agent
    if google_services is None:
        if not os.path.exists('credentials.json'):
            raise Exception("credentials.json missing. Please upload it to the server.")
        google_services = GoogleServices()
        agent = SchedulerAgent(google_services)
        job_agent = JobAgent(google_services)
    return google_services, agent, job_agent

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
                    _, _, _, reply_subject, reply_body, _ = reply_info
                    snippet = reply_body[:150] if reply_body else ""
                    sentiment_obj = google_services.classify_email(reply_subject, snippet)
                
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
                if lbl in ["Positive Follow-up", "Offer Stage"]:
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
