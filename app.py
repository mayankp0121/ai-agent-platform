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
        return jsonify({"response": response})
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

if __name__ == '__main__':
    # Ensure templates directory exists
    if not os.path.exists('templates'):
        os.makedirs('templates')
    
    print("Starting Web Interface on http://localhost:5000")
    app.run(host='0.0.0.0', port=5000, debug=True)
