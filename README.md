# job-search

An automated job discovery and application assistant for contract roles. Runs daily, scores opportunities with Claude AI, generates cover letters for strong matches, and delivers an HTML email digest.

---

## 🔍 What It Does

- Searches JSearch (RapidAPI) across 6 targeted queries for contract/remote roles
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
