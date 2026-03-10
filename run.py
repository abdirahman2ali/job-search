#!/usr/bin/env python3
"""
Job Search Agent
Searches JSearch API, scores with Claude, sends email digest.
Run daily via launchd.
"""

import json
import os
import smtplib
import subprocess
import sys
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
from dotenv import load_dotenv

# --- Paths ---
AGENT_DIR = Path(__file__).parent
DATA_DIR = AGENT_DIR / "data"
SEEN_JOBS_PATH = DATA_DIR / "seen_jobs.json"
LAST_REPORT_PATH = DATA_DIR / "last_report.json"
PROMPT_PATH = AGENT_DIR / "prompt.md"
APPLIED_JOBS_PATH = DATA_DIR / "applied_jobs.json"
COVER_LETTER_PROMPT_PATH = AGENT_DIR / "cover_letter_prompt.md"
AUTO_APPLY_THRESHOLD = 8.5

# --- Config ---
load_dotenv(AGENT_DIR / ".env")
RAPIDAPI_KEY = os.environ["RAPIDAPI_KEY"]
GMAIL_ADDRESS = os.environ["GMAIL_ADDRESS"]
GMAIL_APP_PASSWORD = "".join(c for c in os.environ["GMAIL_APP_PASSWORD"] if c.isalnum())
RECIPIENT = os.environ.get("RECIPIENT_EMAIL", "abdirahman2ali@gmail.com")

JSEARCH_QUERIES = [
    "Analytics Engineer contract remote upwork",
    "dbt Analytics Engineer contract upwork",
    "Data Engineer contract remote upwork",
    "Analytics Engineer freelance upwork",
    "Analytics Engineer contract remote",
    "dbt Analytics Engineer contract",
]


# --- Deduplication ---

def load_seen_jobs() -> set[str]:
    if SEEN_JOBS_PATH.exists():
        return set(json.loads(SEEN_JOBS_PATH.read_text()))
    return set()


def save_seen_jobs(seen: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    SEEN_JOBS_PATH.write_text(json.dumps(sorted(seen), indent=2))


# --- Cover Letter ---

def load_applied_jobs() -> set[str]:
    if APPLIED_JOBS_PATH.exists():
        return set(json.loads(APPLIED_JOBS_PATH.read_text()))
    return set()


def save_applied_jobs(applied: set[str]) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    APPLIED_JOBS_PATH.write_text(json.dumps(sorted(applied), indent=2))


def generate_cover_letter(job: dict) -> str:
    template = COVER_LETTER_PROMPT_PATH.read_text()
    prompt = template.format(
        job_title=job.get("title", ""),
        company=job.get("company", ""),
        job_description=(job.get("description") or "")[:3000],
    )
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        ["/opt/homebrew/bin/claude", "-p", prompt],
        capture_output=True,
        text=True,
        timeout=90,
        env=env,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Cover letter generation failed:\n{result.stderr}")
    return result.stdout.strip()


# --- Job Search ---

def search_jobs() -> list[dict]:
    seen_ids: set[str] = set()
    jobs: list[dict] = []

    for query in JSEARCH_QUERIES:
        try:
            resp = requests.get(
                "https://jsearch.p.rapidapi.com/search",
                headers={
                    "X-RapidAPI-Key": RAPIDAPI_KEY,
                    "X-RapidAPI-Host": "jsearch.p.rapidapi.com",
                },
                params={
                    "query": query,
                    "employment_types": "CONTRACTOR",
                    "remote_jobs_only": "true",
                    "num_pages": "2",
                    "date_posted": "week",
                },
                timeout=30,
            )
            resp.raise_for_status()

            for j in resp.json().get("data", []):
                job_id = j.get("job_id")
                if not job_id or job_id in seen_ids:
                    continue
                seen_ids.add(job_id)
                jobs.append({
                    "id": job_id,
                    "title": j.get("job_title", ""),
                    "company": j.get("employer_name", ""),
                    "location": j.get("job_city") or "Remote",
                    "description": (j.get("job_description") or "")[:2500],
                    "apply_link": j.get("job_apply_link", ""),
                    "employment_type": j.get("job_employment_type", ""),
                    "is_remote": j.get("job_is_remote", False),
                    "salary_min": j.get("job_min_salary"),
                    "salary_max": j.get("job_max_salary"),
                    "salary_period": j.get("job_salary_period"),
                    "posted_at": j.get("job_posted_at_datetime_utc"),
                    "required_skills": j.get("job_required_skills") or [],
                })

        except Exception as e:
            print(f"  ⚠️  JSearch error for '{query}': {e}")

    return jobs


# --- Scoring via Claude ---

def score_with_claude(jobs: list[dict]) -> list[dict]:
    prompt_template = PROMPT_PATH.read_text()

    jobs_slim = [
        {k: j[k] for k in ("id", "title", "company", "location", "employment_type", "description", "required_skills")}
        for j in jobs
    ]

    full_prompt = (
        f"{prompt_template}\n\n"
        f"## Jobs to Evaluate\n\n"
        f"```json\n{json.dumps(jobs_slim, indent=2)}\n```"
    )

    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    result = subprocess.run(
        ["/opt/homebrew/bin/claude", "-p", full_prompt],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
    )

    if result.returncode != 0:
        raise RuntimeError(f"claude -p failed:\n{result.stderr}")

    raw = result.stdout.strip()
    # Extract the JSON array robustly — find the first '[' and last ']'
    start = raw.find("[")
    end = raw.rfind("]")
    if start == -1 or end == -1:
        raise RuntimeError(f"No JSON array found in Claude output:\n{raw}")
    raw = raw[start:end + 1]

    scored: list[dict] = json.loads(raw)

    job_map = {j["id"]: j for j in jobs}
    output = []
    for s in scored[:5]:
        job = dict(job_map.get(s["id"], {}))
        if not job:
            print(f"  ⚠️  Unknown job ID from Claude: {s.get('id')} — skipping")
            continue
        job.update({
            "score": s.get("score"),
            "fit_summary": s.get("fit_summary", ""),
            "key_match_skills": s.get("key_match_skills", []),
            "potential_concern": s.get("potential_concern"),
        })
        output.append(job)

    output.sort(key=lambda x: x.get("score", 0), reverse=True)
    return output


# --- Email ---

def _salary_display(job: dict) -> str:
    sal_min, sal_max = job.get("salary_min"), job.get("salary_max")
    period = (job.get("salary_period") or "yr").lower()
    if sal_min and sal_max:
        return f"${int(sal_min):,} – ${int(sal_max):,} / {period}"
    if sal_min:
        return f"From ${int(sal_min):,} / {period}"
    return ""


def _skill_pills(skills: list[str]) -> str:
    return "".join(
        f"<span style='display:inline-block;background:#dbeafe;color:#1d4ed8;"
        f"padding:3px 10px;border-radius:20px;font-size:12px;font-weight:500;"
        f"margin:2px 4px 2px 0'>{s}</span>"
        for s in skills
    )


def _cover_letter_block(cover_letter: Optional[str]) -> str:
    if not cover_letter:
        return ""
    return (
        "<div style='margin-top:16px;background:#fefce8;border:1px solid #fde047;"
        "border-radius:8px;padding:16px 20px'>"
        "<p style='margin:0 0 10px;color:#854d0e;font-size:13px;font-weight:700'>"
        "Ready to Apply — Cover Letter"
        "</p>"
        f"<pre style='margin:0;background:#fff;border:1px solid #e5e7eb;border-radius:6px;"
        f"padding:14px;font-size:13px;line-height:1.65;white-space:pre-wrap;"
        f"font-family:Georgia,serif;color:#111827'>{cover_letter}</pre>"
        "<p style='margin:10px 0 0;font-size:12px;color:#92400e'>"
        "Resume available on request — attach Notion resume PDF when applying."
        "</p>"
        "</div>"
    )


def _job_card(rank: int, job: dict, cover_letter: Optional[str] = None) -> str:
    salary = _salary_display(job)
    concern = job.get("potential_concern")
    posted = (job.get("posted_at") or "")[:10]

    salary_block = (
        f"<p style='margin:6px 0 12px;font-size:13px;color:#16a34a;font-weight:600'>💰 {salary}</p>"
        if salary else ""
    )
    concern_block = (
        f"<p style='margin:10px 0 0;background:#fef3c7;color:#92400e;"
        f"padding:8px 12px;border-radius:6px;font-size:13px'>⚠️ {concern}</p>"
        if concern else ""
    )
    posted_block = (
        f"<span style='color:#9ca3af;font-size:12px'>Posted {posted}</span>"
        if posted else ""
    )

    return f"""
<div style='background:#ffffff;border:1px solid #e5e7eb;border-radius:12px;
            padding:24px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,0.06)'>
  <div style='margin-bottom:10px'>
    <span style='background:#f0fdf4;color:#15803d;font-size:11px;font-weight:700;
                 padding:3px 10px;border-radius:20px;text-transform:uppercase'>
      #{rank} · Match {job.get("score")}/10
    </span>
  </div>
  <h2 style='margin:8px 0 4px;font-size:18px;color:#111827;font-weight:700'>{job.get("title", "")}</h2>
  <p style='margin:0 0 4px;color:#6b7280;font-size:14px'>
    {job.get("company", "")} &nbsp;·&nbsp; {job.get("location", "Remote")}
  </p>
  {posted_block}
  {salary_block}
  <p style='margin:12px 0 10px;color:#374151;font-size:14px;line-height:1.65'>
    {job.get("fit_summary", "")}
  </p>
  <div style='margin:10px 0'>{_skill_pills(job.get("key_match_skills", []))}</div>
  {concern_block}
  <a href='{job.get("apply_link", "#")}'
     style='display:inline-block;margin-top:16px;background:#111827;color:#ffffff;
            padding:10px 22px;border-radius:8px;text-decoration:none;
            font-size:14px;font-weight:600'>
    View &amp; Apply →
  </a>
  {_cover_letter_block(cover_letter)}
</div>"""


def build_html(jobs: list[dict], applications: Optional[dict] = None) -> str:
    applications = applications or {}
    today = datetime.now().strftime("%B %d, %Y")
    cards = "".join(_job_card(i + 1, j, applications.get(j["id"])) for i, j in enumerate(jobs))
    banner = ""
    if applications:
        n = len(applications)
        banner = (
            f"<div style='background:#1d4ed8;color:#fff;border-radius:12px;"
            f"padding:16px 24px;margin-bottom:20px'>"
            f"<p style='margin:0 0 4px;font-size:14px;font-weight:700'>"
            f"Auto-Apply: {n} cover letter(s) ready"
            f"</p>"
            f"<p style='margin:0;font-size:13px;opacity:0.85'>"
            f"Jobs scoring 8.5+ — cover letters embedded below each card"
            f"</p>"
            f"</div>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style='margin:0;padding:0;background:#f3f4f6;
             font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif'>
  <div style='max-width:640px;margin:40px auto;padding:0 16px 40px'>
    <div style='background:linear-gradient(135deg,#111827 0%,#1f2937 100%);
                border-radius:16px 16px 0 0;padding:32px 32px 28px'>
      <p style='margin:0 0 6px;color:#6ee7b7;font-size:12px;font-weight:600;
                letter-spacing:1px;text-transform:uppercase'>Daily Job Scout</p>
      <h1 style='margin:0;color:#ffffff;font-size:24px;font-weight:700'>Your Top Contract Roles</h1>
      <p style='margin:8px 0 0;color:#9ca3af;font-size:14px'>
        {today} &nbsp;·&nbsp; Analytics Engineer &nbsp;·&nbsp; Remote &nbsp;·&nbsp; ~20 hrs/wk
      </p>
    </div>
    <div style='background:#f3f4f6;padding:24px 0'>{banner}{cards}</div>
    <div style='background:#ffffff;border:1px solid #e5e7eb;border-radius:0 0 16px 16px;
                padding:20px 32px;text-align:center'>
      <p style='margin:0;color:#9ca3af;font-size:12px'>
        Sent by your Job Search Agent &nbsp;·&nbsp; Powered by Claude + JSearch
      </p>
    </div>
  </div>
</body>
</html>"""


def send_error_email(error: Exception) -> None:
    today = datetime.now().strftime("%b %d, %Y %H:%M")
    import traceback
    tb = traceback.format_exc()
    html = f"""<!DOCTYPE html>
<html><body style='font-family:sans-serif;max-width:600px;margin:40px auto;padding:0 16px'>
  <div style='background:#dc2626;color:#fff;padding:20px 24px;border-radius:12px 12px 0 0'>
    <p style='margin:0 0 4px;font-size:12px;font-weight:700;letter-spacing:1px;text-transform:uppercase;opacity:0.8'>Job Scout Alert</p>
    <h1 style='margin:0;font-size:20px'>Agent Failed &mdash; {today}</h1>
  </div>
  <div style='border:1px solid #e5e7eb;border-top:none;border-radius:0 0 12px 12px;padding:20px 24px'>
    <p style='color:#374151;font-size:14px'><strong>Error:</strong> {type(error).__name__}: {error}</p>
    <pre style='background:#f3f4f6;padding:14px;border-radius:8px;font-size:12px;white-space:pre-wrap;color:#111827'>{tb}</pre>
    <p style='color:#9ca3af;font-size:12px;margin-top:16px'>Sent by Job Scout Agent</p>
  </div>
</body></html>"""

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"Job Scout Error - {today}"
    msg["From"] = f"Job Scout <{GMAIL_ADDRESS}>"
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT, msg.as_string())


def send_email(jobs: list[dict], applications: Optional[dict] = None) -> None:
    today = datetime.now().strftime("%b %d")
    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📬 Job Scout Report · {today} · {len(jobs)} matches"
    msg["From"] = f"Job Scout <{GMAIL_ADDRESS}>"
    msg["To"] = RECIPIENT
    msg.attach(MIMEText(build_html(jobs, applications), "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(GMAIL_ADDRESS, GMAIL_APP_PASSWORD)
        server.sendmail(GMAIL_ADDRESS, RECIPIENT, msg.as_string())

    print(f"  ✅ Email sent → {RECIPIENT}")


# --- Main ---

def main() -> None:
    print("=" * 50)
    print("🔍 Job Search Agent")
    print("=" * 50)

    seen_ids = load_seen_jobs()
    print(f"\n📋 Seen jobs: {len(seen_ids)}")

    print("\n🌐 Searching JSearch...")
    all_jobs = search_jobs()
    new_jobs = [j for j in all_jobs if j["id"] not in seen_ids]
    print(f"   Fetched: {len(all_jobs)} | New: {len(new_jobs)}")

    if not new_jobs:
        print("\n⏭️  No new jobs today — skipping.")
        return

    print(f"\n🤖 Scoring {len(new_jobs)} jobs with Claude...")
    top_jobs = score_with_claude(new_jobs)
    print(f"   Selected: {len(top_jobs)}")
    for i, j in enumerate(top_jobs, 1):
        print(f"   #{i} [{j.get('score')}/10] {j.get('title')} @ {j.get('company')}")

    if not top_jobs:
        print("\n⏭️  No qualifying jobs — skipping.")
        return

    # --- Cover letter generation for high scorers ---
    applied_ids = load_applied_jobs()
    applications: dict[str, str] = {}  # job_id -> cover_letter text

    high_scorers = [j for j in top_jobs if (j.get("score") or 0) >= AUTO_APPLY_THRESHOLD]
    print(f"\n✍️  Cover letters: {len(high_scorers)} jobs above {AUTO_APPLY_THRESHOLD} threshold")

    for job in high_scorers:
        if job["id"] in applied_ids:
            print(f"   ⏭️  Already processed: {job.get('title')}")
            continue
        print(f"   Generating for: {job.get('title')} @ {job.get('company')}")
        try:
            cl = generate_cover_letter(job)
            applications[job["id"]] = cl
            applied_ids.add(job["id"])
            print(f"   ✅ Done")
        except Exception as e:
            print(f"   ⚠️  Failed: {e}")

    save_applied_jobs(applied_ids)
    print(f"💾 applied_jobs.json updated ({len(applied_ids)} total)")

    # --- Email ---
    print("\n📧 Sending email...")
    send_email(top_jobs, applications)

    LAST_REPORT_PATH.write_text(json.dumps(top_jobs, indent=2))
    print(f"💾 last_report.json saved")

    updated_seen = seen_ids | {j["id"] for j in all_jobs}
    save_seen_jobs(updated_seen)
    print(f"💾 seen_jobs.json updated ({len(updated_seen)} total)")

    print("\n✅ Done!")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"  💥 Fatal error: {e}")
        try:
            send_error_email(e)
            print(f"  📧 Error email sent → {RECIPIENT}")
        except Exception as email_err:
            print(f"  ⚠️  Could not send error email: {email_err}")
        sys.exit(1)
