"""
create_intake_files.py
═══════════════════════════════════════════════════════════════
Pre-fill Bot — Run ONCE before your app owner interview days.

What it does:
  1. Reads your app list from DataSight + Teambook (or a seed CSV)
  2. Creates one pre-filled YAML intake file per app in:
       registry/intake/{app-id}-intake.yaml
  3. Commits all files to a branch: intake/batch-{date}
  4. Emails each team lead with:
       • Their current RF and LTDD from DataSight (shows you did homework)
       • A DIRECT link to their specific file in GitHub web editor
       • 3-step instructions (no Git knowledge required)
       • Their pre-filled answers shown inline so they just fix the gaps

App owner experience:
  1. Receives email with direct link
  2. Clicks link → GitHub opens their file in web editor (pre-filled)
  3. They fill the blank fields (all clearly marked)
  4. Click green "Commit changes" button — done. No Git knowledge needed.

Usage:
  python create_intake_files.py                    # full run (DataSight + Teambook)
  python create_intake_files.py --seed seed.csv    # use a CSV seed file instead
  python create_intake_files.py --dry-run          # preview emails, don't send
  python create_intake_files.py --app payments-api # single app only
"""

import argparse
import csv
import json
import logging
import os
import smtplib
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import requests
import yaml

logger = logging.getLogger(__name__)

ROOT       = Path(__file__).parent.parent
INTAKE_DIR = ROOT / "registry" / "intake"
CONFIG     = ROOT / "config" / "settings.yaml"


# ── Config ────────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(CONFIG) as f:
        return yaml.safe_load(f)


# ── DataSight connector ───────────────────────────────────────────────────────

class DataSightConnector:
    """
    Pulls current RF and LTDD per app from DataSight API.
    These pre-populate the intake file so app owners see their real numbers.
    """

    def __init__(self, cfg: dict):
        self.base_url = cfg.get("datasight", {}).get("base_url", "")
        self.token    = os.environ.get(
            cfg.get("datasight", {}).get("token_env", "DATASIGHT_TOKEN"), ""
        )
        self._cache: dict[str, dict] = {}

    def get_app_metrics(self, app_name: str) -> dict:
        """Returns {rf_current, ltdd_current, cfr, mttr} for an app."""
        if app_name in self._cache:
            return self._cache[app_name]

        if not self.base_url or not self.token:
            logger.debug(f"DataSight not configured — using placeholder for {app_name}")
            return {"rf_current": None, "ltdd_current": None, "cfr": None, "mttr": None}

        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/apps/{app_name}/metrics",
                headers={"Authorization": f"Bearer {self.token}"},
                params={"period": "last_30_days"},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            result = {
                "rf_current":   data.get("deployment_frequency"),
                "ltdd_current": data.get("lead_time_days"),
                "cfr":          data.get("change_failure_rate"),
                "mttr":         data.get("mean_time_to_restore_hours"),
                "datasight_app_id": data.get("app_id", ""),
            }
            self._cache[app_name] = result
            return result
        except Exception as e:
            logger.warning(f"DataSight fetch failed for {app_name}: {e}")
            return {"rf_current": None, "ltdd_current": None, "cfr": None, "mttr": None}

    def list_all_apps(self) -> list[dict]:
        """Returns all apps registered in DataSight."""
        if not self.base_url or not self.token:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/api/v1/apps",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("apps", [])
        except Exception as e:
            logger.warning(f"DataSight app list failed: {e}")
            return []


# ── Teambook connector ────────────────────────────────────────────────────────

class TeambookConnector:
    """
    Pulls team/pod details per app from Teambook.
    Pre-populates team_lead_email, team_id, release_champion.
    """

    def __init__(self, cfg: dict):
        self.base_url = cfg.get("teambook", {}).get("base_url", "")
        self.token    = os.environ.get(
            cfg.get("teambook", {}).get("token_env", "TEAMBOOK_TOKEN"), ""
        )
        self._cache: dict[str, dict] = {}

    def get_team_for_app(self, app_name: str) -> dict:
        if app_name in self._cache:
            return self._cache[app_name]

        if not self.base_url or not self.token:
            return {}

        try:
            resp = requests.get(
                f"{self.base_url}/api/teams/by-app/{app_name}",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            result = {
                "team_id":          data.get("team_id", ""),
                "team_lead_email":  data.get("team_lead", {}).get("email", ""),
                "team_lead_name":   data.get("team_lead", {}).get("name", ""),
                "teambook_team_id": data.get("id", ""),
            }
            self._cache[app_name] = result
            return result
        except Exception as e:
            logger.warning(f"Teambook fetch failed for {app_name}: {e}")
            return {}

    def list_all_teams(self) -> list[dict]:
        if not self.base_url or not self.token:
            return []
        try:
            resp = requests.get(
                f"{self.base_url}/api/teams",
                headers={"Authorization": f"Bearer {self.token}"},
                timeout=20,
            )
            resp.raise_for_status()
            return resp.json().get("teams", [])
        except Exception as e:
            logger.warning(f"Teambook team list failed: {e}")
            return []


# ── Seed CSV reader (fallback when APIs not ready) ────────────────────────────

def read_seed_csv(path: str) -> list[dict]:
    """
    Read a seed CSV file with columns:
    app_id, display_name, team_lead_email, stack, app_type, datasight_app_name
    (All other fields get sensible defaults in the generated intake file.)
    """
    apps = []
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            apps.append({k.strip(): v.strip() for k, v in row.items()})
    return apps


# ── Intake YAML builder ───────────────────────────────────────────────────────

def build_intake_yaml(
    app_id: str,
    display_name: str,
    team_lead_email: str,
    ds_metrics: dict,
    tb_data: dict,
    known_stack: str = "",
    known_app_type: str = "",
) -> str:
    """
    Builds the pre-filled YAML content for one app.
    Fields we KNOW are pre-filled. Fields we DON'T KNOW are marked [FILL THIS].
    Returns the YAML string.
    """

    rf_str   = f"{ds_metrics['rf_current']:.1f} releases/month" if ds_metrics.get("rf_current") else "[FILL THIS — or check DataSight]"
    ltdd_str = f"{ds_metrics['ltdd_current']:.1f} days" if ds_metrics.get("ltdd_current") else "[FILL THIS — or check DataSight]"
    lead     = team_lead_email or tb_data.get("team_lead_email", "[FILL THIS]")
    ds_name  = ds_metrics.get("datasight_app_id", display_name)
    stack    = known_stack or "[FILL THIS — java-spring | dotnet | node | python | iSeries | vendor | mixed]"
    app_type = known_app_type or "[FILL THIS — modern | traditional | legacy | vendor]"

    content = f"""# ═══════════════════════════════════════════════════════════════
# DPI APP REGISTRY — INTAKE FILE
# App: {display_name}
# ═══════════════════════════════════════════════════════════════
#
# HOW TO FILL THIS FILE:
#   1. Look for fields marked [FILL THIS] — those need your input.
#   2. Fields already filled are from DataSight/Teambook — correct if wrong.
#   3. When done: scroll to bottom, click "Commit changes", done.
#      No Git knowledge needed beyond clicking that one button.
#
# CURRENT METRICS (from DataSight — for your awareness):
#   Release Frequency : {rf_str}
#   Lead Time (LTDD)  : {ltdd_str}
# ═══════════════════════════════════════════════════════════════

# ── IDENTITY (pre-filled — correct if wrong) ──────────────────
app_id: "{app_id}"
display_name: "{display_name}"
datasight_app_name: "{ds_name}"
teambook_team_id: "{tb_data.get('teambook_team_id', '')}"

# ── TEAM ──────────────────────────────────────────────────────
team_lead_email: "{lead}"
release_champion: "{lead}"   # Change this if someone else drives releases

# ── APP CLASSIFICATION ────────────────────────────────────────
# app_type options: modern | traditional | legacy | vendor
#   modern       = Cloud-native, full CI/CD possible
#   traditional  = On-prem/hybrid, CI possible, CD needs work
#   legacy       = Infrequent releases, patch-driven
#   vendor       = Release schedule controlled by vendor/iSeries
#                  (RF will be EXCLUDED from portfolio scoring for vendor apps)
app_type: "{app_type}"

# stack options: java-spring | dotnet | node | python | iSeries | vendor | mixed | other
stack: "{stack}"

in_production: true    # Change to false if this app is not yet live in prod
tier: 2                # 1=mission-critical  2=important  3=standard

# ── GIT REPOSITORIES ──────────────────────────────────────────
# IMPORTANT: Git Org is created FIRST — it is the container.
# Repos are created INSIDE the org.
# List every repo this app uses. Mark exactly ONE as is_primary: true.
# If repos span multiple Git orgs, list all of them.
#
# Example:
#   - git_org: "myorg"          ← the GitHub organisation (container)
#     repo_name: "payments-api" ← the repo inside that org
#     role: "backend"
#     is_primary: true
#   - git_org: "infra-org"      ← a DIFFERENT org (still supported)
#     repo_name: "payments-infra"
#     role: "infra"
#     is_primary: false

repos:
  - git_org: "[FILL THIS — your GitHub org name]"
    repo_name: "[FILL THIS — your main repo name]"
    role: "backend"     # backend | frontend | infra | shared-lib | config
    is_primary: true    # This repo drives LTDD measurement

# Add more repos below if needed:
# - git_org: ""
#   repo_name: ""
#   role: ""
#   is_primary: false

# ── PIPELINE FLAGS (Y/N — answer honestly) ────────────────────
# Each "false → true" flip = immediate DPI Automation score improvement.
ci_automated: false              # Does CI trigger automatically on every push?
cd_automated: false              # Does deployment trigger automatically after CI?
standard_pipeline_adopted: false # Are you on the org standard pipeline template?
git_hygiene_adopted: false       # Has the team adopted git branch/PR standards?
cr_auto_creation: false          # Are change requests auto-raised in ServiceNow?
zero_touch_deployment: false     # Can you deploy with NO human intervention?
automated_rollback: false        # Is rollback fully automated?
feature_flags_adopted: false     # Are feature flags used for safe prod releases?
                                 # NOTE: Feature flags are an architectural pattern
                                 # to enable SMALLER, MORE FREQUENT production deploys.
                                 # They do NOT count as releases by themselves.
                                 # Only production deployments count for RF.

approval_gate_count: 3           # [FILL THIS] How many manual human approvals
                                 # are required before a production deploy?

# ── ACCESS & SECURITY ─────────────────────────────────────────
priv_access_for_deploy: true     # [FILL THIS] Does deploying to prod require
                                 # elevated/privileged access? true = YES (score deduction)
                                 # This is the #1 zero-touch blocker — be honest.

priv_access_reviewed_date: ""    # [FILL THIS] YYYY-MM-DD of last formal review
                                 # Must be within 90 days for full Stability score.

sast_enabled: false              # Is SAST/DAST security scanning in your pipeline?
data_classification: "internal"  # public | internal | restricted | confidential

# ── TOOLING IDs (Person 3 will complete — leave blank if unsure) ──
servicenow_ci_id: ""             # ServiceNow CI sys_id
jenkins_job_prefix: ""           # e.g. "payments-" matches payments-build, payments-deploy
jira_project_key: ""             # e.g. "PAY"
sonarqube_project: ""            # SonarQube project key

# ── COMPLIANCE ────────────────────────────────────────────────
release_page_url: ""             # [FILL THIS] Confluence/SharePoint URL for release notes
compliance_evidence_page: ""     # [FILL THIS] Compliance evidence URL (required for Tier-1)

# ── AI & ADOPTION ─────────────────────────────────────────────
copilot_enabled: false           # Does team have GitHub Copilot / any AI coding agent?
ai_tools_declared: []            # e.g. ["github-copilot", "cursor"]
apis_published: false            # Are this app's APIs in the org API catalog?
catalog_url: ""
api_count: 0

# ── INTERVIEW CAPTURE (Champion fills during the 1:1 call) ────
biggest_release_blocker: ""      # [FILL DURING CALL] Their exact words
agreed_rf_target_per_month: 0    # [FILL DURING CALL] Target you co-created together
improvement_action_1: ""         # [FILL DURING CALL] First concrete action
improvement_action_2: ""
improvement_action_3: ""
notes: ""                        # Any context useful for scoring or planning
"""
    return content


# ── GitHub file creator ───────────────────────────────────────────────────────

class GitHubIntakeCreator:
    """
    Creates intake YAML files directly in the GitHub repo
    and returns a direct web editor URL for each file.
    """

    def __init__(self, cfg: dict):
        self.base_url = cfg["github"]["base_url"].rstrip("/")
        self.token    = os.environ.get(cfg["github"]["token_env"], "")
        self.org      = cfg["github"]["org"]
        self.registry_repo = cfg["github"].get("registry_repo", "app-registry")
        self.branch   = f"intake/batch-{datetime.now().strftime('%Y-%m-%d')}"
        self._branch_created = False
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"token {self.token}",
            "Accept":        "application/vnd.github.v3+json",
        })

    def _ensure_branch(self):
        if self._branch_created:
            return
        # Get default branch SHA
        resp = self.session.get(
            f"{self.base_url}/repos/{self.org}/{self.registry_repo}/git/refs/heads/main",
            timeout=15,
        )
        sha = resp.json().get("object", {}).get("sha", "")
        if not sha:
            # try master
            resp = self.session.get(
                f"{self.base_url}/repos/{self.org}/{self.registry_repo}/git/refs/heads/master",
                timeout=15,
            )
            sha = resp.json().get("object", {}).get("sha", "")

        if sha:
            self.session.post(
                f"{self.base_url}/repos/{self.org}/{self.registry_repo}/git/refs",
                json={"ref": f"refs/heads/{self.branch}", "sha": sha},
                timeout=15,
            )
        self._branch_created = True

    def create_intake_file(self, app_id: str, content: str) -> Optional[str]:
        """
        Creates the file in GitHub and returns the direct web editor URL.
        Returns None if creation fails.
        """
        import base64
        self._ensure_branch()

        file_path = f"registry/intake/{app_id}-intake.yaml"
        encoded   = base64.b64encode(content.encode()).decode()

        resp = self.session.put(
            f"{self.base_url}/repos/{self.org}/{self.registry_repo}/contents/{file_path}",
            json={
                "message": f"intake: create pre-filled intake for {app_id}",
                "content": encoded,
                "branch":  self.branch,
            },
            timeout=20,
        )

        if resp.status_code in (200, 201):
            # Build direct web editor URL
            ghe_host = self.base_url.replace("/api/v3", "").replace("/api/v3/", "")
            editor_url = (
                f"{ghe_host}/{self.org}/{self.registry_repo}/edit/{self.branch}"
                f"/{file_path}"
            )
            return editor_url
        else:
            logger.error(f"GitHub file creation failed for {app_id}: {resp.status_code} {resp.text[:200]}")
            return None


# ── Email sender ──────────────────────────────────────────────────────────────

def build_email(
    app_id: str,
    display_name: str,
    team_lead_email: str,
    editor_url: str,
    rf_current,
    ltdd_current,
    champion_email: str,
) -> tuple[str, str]:
    """Builds subject and HTML body for the intake invitation email."""

    rf_display   = f"{rf_current:.1f}/month" if rf_current else "not yet measured"
    ltdd_display = f"{ltdd_current:.1f} days" if ltdd_current else "not yet measured"

    subject = f"Action Needed: DPI Registry — {display_name} (5 min task)"

    html = f"""<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body style="font-family:Arial,sans-serif;background:#f5f6fa;margin:0;padding:20px;">
<div style="max-width:640px;margin:0 auto;background:#fff;border-radius:8px;
            box-shadow:0 2px 8px rgba(0,0,0,0.1);overflow:hidden;">

  <!-- Header -->
  <div style="background:#1F3864;padding:28px 32px;">
    <h1 style="color:#fff;margin:0;font-size:20px;">📋 DPI App Registry — Your Input Needed</h1>
    <p style="color:#A9C4E0;margin:6px 0 0;font-size:13px;">{display_name} &nbsp;|&nbsp; 5 minutes &nbsp;|&nbsp; No Git knowledge required</p>
  </div>

  <!-- Current metrics banner -->
  <div style="background:#EBF3FB;padding:16px 32px;display:flex;gap:32px;">
    <div>
      <div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;">Your Current RF</div>
      <div style="font-size:28px;font-weight:bold;color:#c0392b;">{rf_display}</div>
    </div>
    <div>
      <div style="font-size:11px;color:#555;text-transform:uppercase;letter-spacing:1px;">Your Current LTDD</div>
      <div style="font-size:28px;font-weight:bold;color:#e67e22;">{ltdd_display}</div>
    </div>
    <div style="margin-left:auto;align-self:center;">
      <div style="font-size:11px;color:#555;margin-bottom:6px;">DPI Target</div>
      <div style="font-size:18px;font-weight:bold;color:#27ae60;">RF: 280 &nbsp;|&nbsp; LTDD: 1.8d</div>
    </div>
  </div>

  <!-- Body -->
  <div style="padding:28px 32px;">
    <p style="color:#2c3e50;font-size:15px;line-height:1.6;margin-bottom:20px;">
      Hi,<br><br>
      As part of the DevOps transformation, we're building the <strong>DPI App Registry</strong> —
      a central file for your app that feeds the weekly performance leaderboard.
      I've pre-filled what I already know. I just need you to fill in the gaps.
    </p>

    <!-- 3 steps -->
    <div style="background:#f8f9fa;border-radius:6px;padding:20px 24px;margin-bottom:24px;">
      <div style="font-weight:700;color:#1F3864;margin-bottom:14px;font-size:14px;">3 STEPS — TAKES 5 MINUTES</div>
      <div style="display:flex;gap:14px;margin-bottom:10px;align-items:flex-start;">
        <div style="background:#1F3864;color:#fff;border-radius:50%;width:24px;height:24px;
                    display:flex;align-items:center;justify-content:center;font-size:12px;
                    font-weight:bold;flex-shrink:0;margin-top:2px;">1</div>
        <div style="font-size:14px;color:#555;">Click the button below → GitHub opens your file in an editor</div>
      </div>
      <div style="display:flex;gap:14px;margin-bottom:10px;align-items:flex-start;">
        <div style="background:#1F3864;color:#fff;border-radius:50%;width:24px;height:24px;
                    display:flex;align-items:center;justify-content:center;font-size:12px;
                    font-weight:bold;flex-shrink:0;margin-top:2px;">2</div>
        <div style="font-size:14px;color:#555;">Find fields marked <strong style="color:#c0392b;">[FILL THIS]</strong> — fill in your answers</div>
      </div>
      <div style="display:flex;gap:14px;align-items:flex-start;">
        <div style="background:#27ae60;color:#fff;border-radius:50%;width:24px;height:24px;
                    display:flex;align-items:center;justify-content:center;font-size:12px;
                    font-weight:bold;flex-shrink:0;margin-top:2px;">3</div>
        <div style="font-size:14px;color:#555;">Scroll to bottom → click the green <strong>"Commit changes"</strong> button. Done.</div>
      </div>
    </div>

    <!-- CTA Button -->
    <div style="text-align:center;margin-bottom:24px;">
      <a href="{editor_url}"
         style="display:inline-block;background:#1F3864;color:#fff;padding:14px 32px;
                border-radius:6px;text-decoration:none;font-weight:bold;font-size:15px;">
        ✏️ Open My App File in GitHub
      </a>
    </div>

    <!-- What if I can't access GitHub -->
    <div style="background:#fff3cd;border-left:4px solid #f59e0b;padding:14px 18px;
                border-radius:0 4px 4px 0;margin-bottom:20px;font-size:13px;color:#7a5000;">
      <strong>Can't access GitHub?</strong> Reply to this email and I'll fill it with you on our call.
      We're meeting all 70 teams over the next 2 days — your slot is already booked.
    </div>

    <p style="font-size:13px;color:#888;line-height:1.6;">
      Your file has been pre-filled with data from DataSight and Teambook.
      The fields marked <strong>[FILL THIS]</strong> are the ones only you can answer —
      things like your Git repos, whether CI/CD is automated, and who your release champion is.
      <br><br>
      This takes 5 minutes and directly determines your team's starting DPI score.
      Teams that complete this before our call will have their baseline score ready to discuss.
    </p>
  </div>

  <!-- Footer -->
  <div style="background:#f5f6fa;padding:16px 32px;font-size:12px;color:#888;border-top:1px solid #eee;">
    Sent by DevOps Champion on behalf of the DPI Platform initiative.
    Questions? Reply to this email or ping me directly.<br>
    CC'd: <a href="mailto:{champion_email}" style="color:#1F3864;">{champion_email}</a>
  </div>
</div>
</body>
</html>"""

    return subject, html


def send_email(
    to: str, subject: str, html: str, cfg: dict, dry_run: bool = False
) -> bool:
    email_cfg = cfg.get("alerts", {}).get("email", {})
    from_addr = email_cfg.get("from_address", "devops-bot@company.com")
    champion  = email_cfg.get("cc_champion", "")
    smtp_host = email_cfg.get("smtp_host", "")
    smtp_port = int(email_cfg.get("smtp_port", 587))
    smtp_user = os.environ.get(email_cfg.get("smtp_user_env", "SMTP_USER"), "")
    smtp_pass = os.environ.get(email_cfg.get("smtp_pass_env", "SMTP_PASS"), "")

    if dry_run:
        print(f"\n{'─'*60}")
        print(f"DRY RUN → To: {to}  |  Subject: {subject[:60]}")
        return True

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = to
    if champion and champion != to:
        msg["Cc"] = champion
    msg.attach(MIMEText(html, "html"))

    recipients = [to] + ([champion] if champion and champion != to else [])
    try:
        with smtplib.SMTP(smtp_host, smtp_port) as s:
            s.ehlo(); s.starttls()
            if smtp_user and smtp_pass:
                s.login(smtp_user, smtp_pass)
            s.sendmail(from_addr, recipients, msg.as_string())
        return True
    except Exception as e:
        logger.error(f"Email failed to {to}: {e}")
        return False


# ── Main orchestrator ─────────────────────────────────────────────────────────

def run(args):
    cfg = load_config()
    ds  = DataSightConnector(cfg)
    tb  = TeambookConnector(cfg)
    gh  = GitHubIntakeCreator(cfg)

    champion_email = cfg.get("alerts", {}).get("email", {}).get("cc_champion", "")

    # ── Discover apps ─────────────────────────────────────────
    apps = []
    if args.seed:
        logger.info(f"Reading apps from seed CSV: {args.seed}")
        apps = read_seed_csv(args.seed)
    else:
        logger.info("Fetching app list from DataSight...")
        ds_apps = ds.list_all_apps()
        tb_teams = tb.list_all_teams()

        # Build a name → team_lead map from Teambook
        tb_by_app: dict[str, dict] = {}
        for team in tb_teams:
            for app in team.get("apps", []):
                tb_by_app[app] = {
                    "team_lead_email":  team.get("lead_email", ""),
                    "teambook_team_id": team.get("id", ""),
                }

        for da in ds_apps:
            name = da.get("name", da.get("app_id", ""))
            apps.append({
                "app_id":       name.lower().replace(" ", "-"),
                "display_name": name,
                "team_lead_email": tb_by_app.get(name, {}).get("team_lead_email", ""),
                "datasight_app_name": name,
                "stack": "",
                "app_type": "",
            })

    if args.app:
        apps = [a for a in apps if args.app.lower() in a["app_id"].lower()]
        if not apps:
            print(f"No apps found matching '{args.app}'")
            sys.exit(1)

    logger.info(f"Processing {len(apps)} apps...")

    stats = {"created": 0, "emailed": 0, "skipped": 0, "errors": 0}
    results = []

    for i, app in enumerate(apps, 1):
        app_id     = app.get("app_id", "").strip()
        name       = app.get("display_name", app_id)
        lead_email = app.get("team_lead_email", "")

        if not app_id:
            logger.warning(f"Row {i}: missing app_id — skipping")
            stats["skipped"] += 1
            continue

        if not lead_email:
            logger.warning(f"{app_id}: no team lead email — creating file but cannot email")

        logger.info(f"[{i}/{len(apps)}] {app_id}...")

        # Get metrics
        ds_metrics = ds.get_app_metrics(app.get("datasight_app_name", name))
        tb_data    = tb.get_team_for_app(name)

        # Build file content
        content = build_intake_yaml(
            app_id       = app_id,
            display_name = name,
            team_lead_email = lead_email or tb_data.get("team_lead_email", ""),
            ds_metrics   = ds_metrics,
            tb_data      = tb_data,
            known_stack  = app.get("stack", ""),
            known_app_type = app.get("app_type", ""),
        )

        # Save locally as backup regardless of GitHub success
        local_path = INTAKE_DIR / f"{app_id}-intake.yaml"
        INTAKE_DIR.mkdir(parents=True, exist_ok=True)
        local_path.write_text(content)

        # Create in GitHub
        editor_url = None
        if not args.local_only:
            editor_url = gh.create_intake_file(app_id, content)
            if editor_url:
                stats["created"] += 1
                logger.info(f"  ✅ GitHub file created")
            else:
                stats["errors"] += 1
                logger.warning(f"  ⚠ GitHub creation failed — file saved locally at {local_path}")
        else:
            editor_url = f"[local only — file at {local_path}]"
            stats["created"] += 1

        # Send email
        if lead_email and editor_url:
            subject, html = build_email(
                app_id        = app_id,
                display_name  = name,
                team_lead_email = lead_email,
                editor_url    = editor_url,
                rf_current    = ds_metrics.get("rf_current"),
                ltdd_current  = ds_metrics.get("ltdd_current"),
                champion_email = champion_email,
            )
            ok = send_email(lead_email, subject, html, cfg, dry_run=args.dry_run)
            if ok:
                stats["emailed"] += 1

        results.append({
            "app_id": app_id,
            "display_name": name,
            "team_lead": lead_email,
            "editor_url": editor_url,
            "rf_current": ds_metrics.get("rf_current"),
            "ltdd_current": ds_metrics.get("ltdd_current"),
        })

        # Polite rate limiting
        if not args.dry_run:
            time.sleep(0.3)

    # Save results log
    log_path = INTAKE_DIR / f"intake_run_{datetime.now().strftime('%Y%m%d_%H%M')}.json"
    log_path.write_text(json.dumps(results, indent=2, default=str))

    print(f"\n{'═'*60}")
    print(f"  INTAKE BOT SUMMARY — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"{'═'*60}")
    print(f"  Apps processed  : {len(apps)}")
    print(f"  Files created   : {stats['created']}  (GitHub + local backup)")
    print(f"  Emails sent     : {stats['emailed']}")
    print(f"  Skipped         : {stats['skipped']}")
    print(f"  Errors          : {stats['errors']}")
    print(f"  Run log         : {log_path}")
    print(f"\n  Next step: run this command after interviews complete:")
    print(f"  python scripts/convert_intake_to_yaml.py\n")


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%H:%M:%S",
    )
    p = argparse.ArgumentParser(description="Create pre-filled intake files and email app owners")
    p.add_argument("--seed",       help="Path to seed CSV (app_id, display_name, team_lead_email, stack, app_type)")
    p.add_argument("--app",        help="Process only one app (partial name match)")
    p.add_argument("--dry-run",    action="store_true", help="Create files but don't send emails")
    p.add_argument("--local-only", action="store_true", help="Skip GitHub — create files locally only")
    run(p.parse_args())
