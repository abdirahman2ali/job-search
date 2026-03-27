# job-search

An automated job discovery and application assistant for contract roles. Runs daily, scores opportunities with Claude AI, generates cover letters for strong matches, and delivers an HTML email digest.

---

## 🔍 What It Does

- Searches JSearch (RapidAPI) across 6 targeted queries for contract/remote Analytics Engineering roles
- Deduplicates results against previously seen jobs
- Scores top candidates using Claude AI based on tech stack fit, contract authenticity, and seniority
- Auto-generates cover letters for jobs scoring ≥ 8.5/10
- Sends a styled HTML email digest with ranked job cards and embedded cover letters

---

## ⚙️ How It Works

```
Daily trigger (launchd)
    ↓
Search JSearch API (6 queries × 2 pages)
    ↓
Filter new jobs (not in seen_jobs.json)
    ↓
Score with Claude (top 5 returned)
    ↓
Generate cover letters (score ≥ 8.5)
    ↓
Build + send HTML email digest
    ↓
Persist state (seen_jobs.json)
```

---

## 🗂 Project Structure

```
job-search/
├── run.py                    # Main orchestration script
├── prompt.md                 # Claude system prompt for job scoring
├── cover_letter_prompt.md    # Claude prompt template for cover letters
├── requirements.txt          # Python dependencies
├── .env                      # Environment variables (not committed)
└── data/
    ├── seen_jobs.json        # Job IDs already fetched (deduplication)
    └── run.log               # Execution log
```

---

## 🛠 Setup

**1. Clone and install dependencies**

```bash
git clone https://github.com/abdirahman2ali/job-search.git
cd job-search
pip install -r requirements.txt
```

**2. Configure environment variables**

Create a `.env` file in the project root:

```bash
RAPIDAPI_KEY=your_rapidapi_key
GMAIL_ADDRESS=you@gmail.com
GMAIL_APP_PASSWORD=your_app_password
RECIPIENT_EMAIL=you@gmail.com   # optional, defaults to GMAIL_ADDRESS
```

| Variable | Description |
|---|---|
| `RAPIDAPI_KEY` | API key from RapidAPI (JSearch) |
| `GMAIL_ADDRESS` | Gmail address used to send the digest |
| `GMAIL_APP_PASSWORD` | Gmail app password (not your account password) |
| `RECIPIENT_EMAIL` | Email address to receive the daily digest (optional) |

**3. Ensure Claude CLI is installed**

```bash
which claude   # should return /opt/homebrew/bin/claude or similar
```

---

## 🚀 Running

```bash
python run.py
```

On success, an HTML email is sent to `RECIPIENT_EMAIL` with the top-scored jobs.

---

## 🕐 Scheduling (macOS launchd)

To run daily automatically, create a launchd plist in `~/Library/LaunchAgents/` pointing to `run.py`. Set the `StartCalendarInterval` to your preferred time.

Example interval for 8 AM daily:

```xml
<key>StartCalendarInterval</key>
<dict>
    <key>Hour</key>
    <integer>8</integer>
    <key>Minute</key>
    <integer>0</integer>
</dict>
```

---

## 🧰 Stack

| Component | Tool |
|---|---|
| Language | Python 3 |
| Job data | JSearch API (RapidAPI) |
| AI scoring + cover letters | Claude CLI (`claude -p`) |
| Email delivery | Gmail SMTP (`smtplib`) |
| Secrets management | `python-dotenv` |
| Scheduling | macOS launchd |
| State persistence | JSON files (`data/`) |
