# DevOps Transformation Platform — Starter Repo

## Start Here

This repo is the complete platform. Everything runs in order from Step 1 to Step 4.
Read this file first. Run nothing until you've filled in `config/settings.yaml`.

---

## Repo Structure

```
devops-platform/
│
├── README.md                          ← You are here
├── requirements.txt                   ← pip install this first
│
├── config/
│   ├── settings.yaml                  ← FILL THIS IN before running anything
│   └── recommendation_rules.yaml      ← Rules engine config (add new rules here)
│
├── registry/
│   ├── apps/                          ← Empty now. Filled by Step 2. One YAML per app.
│   └── intake/
│       ├── _intake_template.yaml      ← Template champion fills during interviews
│       └── _intake_template.md        ← Markdown version for app owners (async)
│
├── scripts/
│   │
│   ├── ── NUMBERED = RUN IN ORDER ──────────────────────────────────────────
│   │
│   ├── 1_create_intake_files.py       ← STEP 1: Creates + emails intake files
│   ├── 2_convert_intake_to_yaml.py    ← STEP 2: Converts intake → registry YAMLs
│   ├── 3_score_engine.py              ← STEP 3: Computes DPI scores per app
│   ├── 4_reporter.py                  ← STEP 4: Generates HTML leaderboard
│   │
│   ├── ── SUPPORT SCRIPTS ──────────────────────────────────────────────────
│   │
│   ├── hygiene_checker.py             ← Git hygiene checks (runs as part of Step 3)
│   ├── registry_loader.py             ← Loads + validates all registry YAMLs
│   ├── recommendation_engine.py       ← Generates top 3 actions per app
│   ├── validate_registry.py           ← CI check — run on every PR to registry
│   │
│   └── connectors/
│       ├── datasight_connector.py     ← Person 1 owns: RF, LTDD, CFR, MTTR
│       ├── github_connector.py        ← Person 2 owns: hygiene, deployments
│       └── teambook_connector.py      ← Person 1 owns: team/pod data
│
└── reports/                           ← All output lands here
    ├── .gitkeep
    ├── latest_hygiene.json            ← Written by hygiene_checker
    ├── latest_metrics.json            ← Written by connectors
    ├── latest_scores.json             ← Written by score_engine
    └── weekly_YYYY-MM-DD.html         ← Written by reporter
```

---

## Setup (Do This First)

```bash
# 1. Clone this repo
git clone https://github.com/YOUR-ORG/devops-platform.git
cd devops-platform

# 2. Install Python dependencies
pip install -r requirements.txt

# 3. Set your API tokens as environment variables
export GITHUB_TOKEN="ghp_your_token_here"
export DATASIGHT_TOKEN="your_datasight_token"
export TEAMBOOK_TOKEN="your_teambook_token"
export SMTP_USER="devops-bot@company.com"
export SMTP_PASS="your_smtp_password"

# 4. Fill in config/settings.yaml
#    Open it. Replace every placeholder marked [FILL THIS].
#    Minimum required: github.base_url, github.org, datasight.base_url,
#    teambook.base_url, alerts.email.smtp_host, alerts.email.from_address
```

---

## Execution Order — Day by Day

### Day 1–2 · Champion runs Step 1, then conducts interviews

```bash
# Creates one pre-filled intake YAML per app in registry/intake/
# Emails each team lead with a direct GitHub link to their file
python scripts/1_create_intake_files.py --dry-run   # preview first
python scripts/1_create_intake_files.py             # send for real

# If you don't have DataSight/Teambook connected yet, use the seed CSV:
python scripts/1_create_intake_files.py --seed registry/intake/seed.csv
```

**During the interview**, open the intake file for that app in GitHub web editor
and fill the `[FILL THIS]` fields live as the conversation happens.
Commit at the end of each call.

---

### After interviews · Person 3 runs Step 2

```bash
# Converts all filled intake files → validated registry/apps/{app-id}.yaml
python scripts/2_convert_intake_to_yaml.py

# Validate the registry is clean
python scripts/validate_registry.py
```

---

### Week 1 · Person 1 connects DataSight + Teambook

```bash
# Test the DataSight connection
python scripts/connectors/datasight_connector.py --test

# Pull all metrics (writes to reports/latest_metrics.json)
python scripts/connectors/datasight_connector.py

# Pull team data from Teambook
python scripts/connectors/teambook_connector.py
```

---

### Week 1 · Person 2 connects GitHub

```bash
# Test GitHub connection
python scripts/connectors/github_connector.py --test

# Run hygiene check across all registered repos
python scripts/hygiene_checker.py
# Output: reports/latest_hygiene.json
```

---

### Week 2 · Person 4 computes scores + publishes leaderboard

```bash
# Compute DPI scores for all apps (needs latest_metrics.json + latest_hygiene.json)
python scripts/3_score_engine.py
# Output: reports/latest_scores.json

# Generate weekly HTML leaderboard
python scripts/4_reporter.py
# Output: reports/weekly_YYYY-MM-DD.html

# Open it:
open reports/weekly_$(date +%Y-%m-%d).html
```

---

## Who Owns What

| Person | Scripts | Focus |
|--------|---------|-------|
| **Person 1** | `datasight_connector.py`, `teambook_connector.py` | Data connections, metrics pipeline |
| **Person 2** | `github_connector.py`, `hygiene_checker.py` | GitHub integration, git hygiene |
| **Person 3** | `1_create_intake_files.py`, `2_convert_intake_to_yaml.py`, `validate_registry.py` | Registry intake, app data |
| **Person 4** | `3_score_engine.py`, `4_reporter.py` | Scoring engine, leaderboard output |

---

## Adding New Scoring Rules

No code change needed. Open `config/recommendation_rules.yaml` and add a rule block:

```yaml
- id: your_new_rule
  condition: "pipeline_flags.your_flag == false"
  action: "What the team should do"
  detail: "Specific guidance on how to do it"
  pillar: Automation
  points_gain: 3
  effort: medium
```

Save it. Next scoring run picks it up automatically.

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| `GITHUB_TOKEN not found` | `export GITHUB_TOKEN=your_token` |
| `DATASIGHT_TOKEN not found` | `export DATASIGHT_TOKEN=your_token` |
| Registry validation fails | Run `python scripts/validate_registry.py` — it tells you exactly which fields are wrong |
| Score shows N/A | Registry entry is incomplete — check that app's YAML in `registry/apps/` |
| No metrics for an app | Check `datasight_app_name` in the registry matches exactly how it appears in DataSight |
| Email not sending | Test SMTP: `python -c "import smtplib; s=smtplib.SMTP('YOUR_HOST',587); s.ehlo(); print('OK')"` |

---

## First Milestone Checklist

```
[ ] config/settings.yaml filled in
[ ] pip install -r requirements.txt done
[ ] Environment variables set
[ ] 1_create_intake_files.py run (intake emails sent)
[ ] All 70 app owner interviews completed
[ ] 2_convert_intake_to_yaml.py run (70 registry files created)
[ ] datasight_connector.py returning data
[ ] github_connector.py returning data
[ ] 3_score_engine.py producing scores
[ ] 4_reporter.py producing leaderboard HTML
[ ] First leaderboard published to all team leads  ← WEEK 2 TARGET
```
