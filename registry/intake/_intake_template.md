# App Registry Intake — {APP NAME HERE}

> **Instructions for app owners:**
> Fill in this file directly in GitHub (click the pencil ✏️ icon to edit).
> Answer each question by replacing the `_answer here_` placeholder.
> When done, scroll down, add commit message: `Intake: {your app name}`, and click **Commit changes**.
> No YAML knowledge required — plain English answers are fine.
> 
> **If you cannot access GitHub:** Save this file filled in to SharePoint folder:
> `DevOps Platform > App Registry Intake > {your app name}.md`
> The automation will pick it up from either location.

---

## 1. App Identity

**App name (short slug, no spaces):**
_answer here_

**Display name (for leaderboard):**
_answer here_

**Your email (team lead):**
_answer here_

**Release champion email** (person driving releases — can be you):
_answer here_

---

## 2. App Classification

**What type of app is this?** (choose one)
- [ ] Modern / Cloud-Native — full CI/CD possible, feature flags viable
- [ ] Traditional / On-Prem — CI possible, CD needs work
- [ ] Legacy / Stabilised — infrequent releases, patch-driven
- [ ] Vendor / iSeries / Fixed — release schedule controlled by vendor *(RF will be excluded from scoring)*

**Primary tech stack:**
_answer here_ *(e.g. Java/Spring, .NET, Node.js, Python, iSeries, Vendor, Mixed)*

**Is this app in production today?**
- [ ] Yes
- [ ] No — it's pre-production (pipeline runs still count toward RF score)

**App tier:**
- [ ] Tier 1 — Mission critical
- [ ] Tier 2 — Important
- [ ] Tier 3 — Standard

---

## 3. Git Repositories

List ALL Git repositories for this app.
For each repo, provide: **Git Org name** (the organisation that contains the repo) and **Repo name**.

> Note: A Git Org is created first and contains repos. e.g. "myorg" is the org, "payments-api" is the repo inside it.

| Git Org (container) | Repo Name | Role | Primary? |
|---------------------|-----------|------|----------|
| _e.g. myorg_ | _e.g. payments-api_ | backend | Yes — this one drives LTDD |
| _e.g. infra-org_ | _e.g. payments-infra_ | infra | No |

*(Add more rows as needed. Mark exactly ONE as Primary.)*

---

## 4. Pipeline Flags (Yes / No)

Answer honestly — these are your improvement levers, not a test.

**CI automated?** (builds trigger automatically on every push)
- [ ] Yes  - [ ] No

**CD automated?** (deployments trigger automatically after CI passes)
- [ ] Yes  - [ ] No

**Using the org standard pipeline template?**
- [ ] Yes  - [ ] No  - [ ] Partially

**Git hygiene formally adopted?** (branch age, PR size, commit standards)
- [ ] Yes  - [ ] No  - [ ] In progress

**Change requests auto-created in ServiceNow?**
- [ ] Yes  - [ ] No

**Zero-touch deployment?** (no human intervention needed to deploy)
- [ ] Yes  - [ ] No

**Automated rollback?**
- [ ] Yes  - [ ] No

**Feature flags adopted?**
- [ ] Yes  - [ ] No

**How many manual human approval gates are in your pipeline today?**
_answer here_ *(count each person/team that must sign off before production)*

---

## 5. Access & Security

**Does deploying to production require someone with elevated/privileged access?**
- [ ] Yes — someone with elevated rights must be involved in the deploy
- [ ] No — any authorised team member can deploy

**When was privileged access last formally reviewed?**
_answer here_ *(YYYY-MM-DD format, or "never reviewed")*

**Is security scanning (SAST/DAST) in your pipeline?**
- [ ] Yes  - [ ] No

**Data classification of this app:**
- [ ] Public  - [ ] Internal  - [ ] Restricted  - [ ] Confidential

---

## 6. Tooling IDs
*(Leave blank if unsure — Person 3 will complete these)*

**How does this app appear in DataSight?** (exact app name in DataSight):
_answer here_

**ServiceNow CI ID** (for incident linkage):
_answer here_

**Jenkins job prefix** (e.g. payments- matches payments-build, payments-deploy):
_answer here_

**Jira project key** (e.g. PAY):
_answer here_

---

## 7. Compliance URLs

**Release notes page URL** (Confluence or SharePoint):
_answer here_

**Compliance evidence page URL**:
_answer here_

---

## 8. AI & Adoption

**Does your team use GitHub Copilot or any AI coding tools?**
- [ ] Yes — officially  - [ ] Yes — informally  - [ ] No

**Which AI tools, if any?** (Copilot, Cursor, other):
_answer here_

**Are your APIs published in the org API catalog?**
- [ ] Yes  - [ ] No

---

## 9. Interview Notes
*(Champion fills this section during the 1:1 call)*

**Biggest blocker to releasing more frequently:**
_answer here_

**Last release description** (what did it involve, end-to-end?):
_answer here_

**Agreed RF target per month:**
_answer here_

**Three improvement actions agreed:**
1. _answer here_
2. _answer here_
3. _answer here_

**Other notes:**
_answer here_
