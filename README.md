# AI Agent Platform: Meeting Scheduler & Job Outreach

A professional-grade AI platform built with OpenAI GPT-4o and Google APIs. It features a modern, glassmorphic web interface and handles complex scheduling and bulk job application tasks.

## 🚀 Features

### 1. Meeting Scheduler Agent
*   **Natural Language Processing**: Schedule meetings using simple English.
*   **Google Calendar Sync**: Automatically creates events with Google Meet links.
*   **Email Confirmations**: Sends professional calendar invites and email summaries.

### 2. Job Application Agent
*   **Bulk CSV Outreach**: Upload a CSV of recruiters and positions for mass personalization.
*   **Resume Intelligence**: Automatically parses your PDF resume to write tailored cover letters.
*   **Template Support**: Personalizes your own cover letter templates using `{name}` and `{position}` placeholders.
*   **Attachments**: Automatically attaches your resume to all outgoing application emails.

## 🛠️ Tech Stack
*   **Backend**: Python, Flask
*   **AI**: OpenAI GPT-4o
*   **Integrations**: Google Calendar API, Gmail API
*   **Frontend**: Vanilla HTML/JS, CSS (Glassmorphism), Lucide Icons
*   **PDF Parsing**: pypdf

## 📦 Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/patidar-mayank/ai-agent-platform.git
   cd ai-agent-platform
   ```

2. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```

3. Setup Environment Variables:
   Create a `.env` file:
   ```env
   OPENAI_API_KEY=your_openai_key
   ```

4. Setup Google Credentials:
   *   Place your `credentials.json` from Google Cloud Console in the root directory.
   *   The first run will prompt you to authorize the app via browser.

## 🖥️ Usage
Run the web interface:
```bash
python3 app.py
```
Visit `http://localhost:5000` to access the dashboard.

---
Built with ❤️ by [Mayank Patidar](https://github.com/patidar-mayank)
