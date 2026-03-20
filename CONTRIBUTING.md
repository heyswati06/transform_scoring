# Contributing & Extending the Platform

This document explains how to extend the platform without breaking anything.
All extensions are config-driven — most require no Python code changes.

---

## Adding a New Scoring Rule

> **No code change required. YAML only.**

1. Open `config/recommendation_rules.yaml`
2. Add a new rule block at the end:

```yaml
- id: unique_rule_id           # snake_case, no spaces
  condition: "pipeline_flags.your_flag == false"
  action: "Short action title shown on score card"
  detail: >
    2–3 sentences of specific guidance. Name the exact steps.
    Reference the registry field name if relevant.
  pillar: Automation           # Velocity | Flow | Stability | Automation | AI & Adoption
  points_gain: 3               # Estimated DPI points gained (honest estimate)
  effort: medium               # low (hours) | medium (days) | high (weeks)
  skip_for_app_types: [vendor] # Optional: skip these stack types
```

3. Save and commit. The rule appears in the next scoring run.

### Condition Syntax

```yaml
# Equality
condition: "pipeline_flags.cd_automated == false"
condition: "access_security.priv_access_for_deploy == true"
condition: "app_type == vendor"
condition: "app_type != vendor"

# Comparison (for numeric fields)
condition: "pipeline_flags.approval_gate_count >= 3"
condition: "score.velocity < 40"

# List membership
condition: "stack in [java-spring, dotnet]"
condition: "app_type not in [vendor, legacy]"

# Compound (AND)
condition: "pipeline_flags.cd_automated == true and pipeline_flags.zero_touch_deployment == false"
```

### Template Variables in Action Text

Use `{field_name}` to insert live values:

```yaml
action: "Reduce approval gates from {approval_gate_count} to 1"
detail: "You currently have {approval_gate_count} gates..."
```

The engine substitutes the actual value from the registry at runtime.

---

## Adding a New DataSight Metric

1. Confirm the exact field name returned by the DataSight API for your metric
2. Add to `config/settings.yaml` under `datasight.field_map`:

```yaml
datasight:
  field_map:
    release_frequency:  "deployment_frequency"  # existing
    ltdd:               "lead_time_days"         # existing
    your_new_metric:    "datasight_field_name"   # add this
```

3. Add fetching in `scripts/connectors/datasight_connector.py` in `get_metrics()`:

```python
return {
    ...existing fields...,
    "your_new_metric": raw.get(self.fmap.get("your_new_metric", "datasight_field_name")),
}
```

4. Add scoring logic in `scripts/3_score_engine.py` inside the relevant pillar method
5. Add a recommendation rule in `config/recommendation_rules.yaml`

---

## Adding a New App to the Registry

```bash
# Option A: Champion fills during interview (preferred)
cp registry/intake/_intake_template.yaml registry/intake/{app-id}-intake.yaml
# Fill it in during the call, then:
python scripts/2_convert_intake_to_yaml.py --app {app-id}

# Option B: Direct registry entry
cp registry/apps/_template.yaml registry/apps/{app-id}.yaml
# Fill all required fields
python scripts/validate_registry.py
# Raise PR → Champion approves
```

---

## Updating an Existing App Entry

Team leads raise a PR directly to `registry/apps/{their-app-id}.yaml`.
The champion reviews and approves via GitHub's CODEOWNERS mechanism.

```bash
# Set up CODEOWNERS (do this once after creating the registry repo)
echo "registry/apps/* @your-github-username" > .github/CODEOWNERS
```

This ensures every change to any app's registry entry is reviewed by the champion.

---

## Adjusting Pillar Weights

In `config/settings.yaml`:

```yaml
scoring:
  weights:
    velocity:    30   # Must sum to 100
    flow:        25
    stability:   20
    automation:  15
    ai_adoption: 10
```

After changing weights, run `python scripts/3_score_engine.py` to see the impact.
All historical scores in the archive JSONs remain on the old weights — only future runs use new weights.

---

## Adding a New Connector (New Data Source)

1. Create `scripts/connectors/your_source_connector.py`
2. Follow the pattern in `datasight_connector.py`:
   - `test()` method to verify connection
   - Returns a JSON-serialisable dict
   - Writes to `reports/latest_{source}.json`
3. Call it from the scheduler or run it manually before `3_score_engine.py`
4. Update `3_score_engine.py` to read the new JSON file

---

## Applying This Architecture to a New Initiative

To reuse this platform for a different measurement initiative:

```
1. Define your registry schema
   → What manual fields does the initiative need from each team?
   → Copy _intake_template.yaml, update the fields

2. Identify your data sources
   → What APIs already exist that have the metrics you need?
   → Create a connector in scripts/connectors/

3. Define your scoring pillars
   → What are the 3–5 dimensions that matter?
   → Update config/settings.yaml weights

4. Write your recommendation rules
   → What are the highest-impact actions per failing metric?
   → Add to config/recommendation_rules.yaml

5. The engine, leaderboard, alerts, and badges are all reusable as-is.
```

**You do not need to rewrite the platform. You extend it.**

---

## Code Style

- Python 3.11+ type hints where practical
- Each script is independently runnable (`if __name__ == "__main__"`)
- All config from `config/settings.yaml` — no hardcoded values in scripts
- Errors are logged, not raised silently — every app produces a result even if partial
- Dry-run mode (`--dry-run`) on any script that sends emails or writes files

---

*Contributing Guide v1.0 · March 2026*
