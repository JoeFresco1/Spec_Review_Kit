# spec_review

A tiny spec-review compiler. Write markdown specs, build one self-contained
HTML file for stakeholders, and collect their feedback as JSON.

No server, no database, no accounts, no dependencies. Python standard library
only. The generated HTML works offline from `file://`.

## Workflow

```
input/*.md  ->  build.py  ->  output/stakeholder-review.html   (send to stakeholder)
                              output/review-data.json          (interchange format)

feedback/*.json  +  output/review-data.json  ->  collect.py  ->  output/review-matrix.csv
                                                                  output/review-digest.md
```

## Quick start

```powershell
python build.py                          # compile the samples in input/
python collect.py --feedback samples     # demo the merge using sample feedback
```

Then open `output/stakeholder-review.html` in a browser.

## Writing specs

Each file in `input/` is one area (H1) with features (H2). Only `#`, `##`, and
`###` headings matter to the build.

| Convention | Meaning |
| --- | --- |
| `stakeholder: true` / `false` in front matter | Include or exclude the whole file |
| `order: 2` in front matter | Sort position (lower first) |
| `# Area` | Top-level group; becomes a reviewable feature if it has no H2s |
| `## Feature` | Reviewable item shown to stakeholders |
| `### Section` | Folded into the feature's details, never separate |
| `## Feature {#custom.id}` | Explicit stable id, recommended for long-lived specs |
| `<!-- internal -->` right after a heading | Drops that section and everything under it |

The first paragraph under a feature becomes the summary stakeholders see. The
rest lives behind "Show details". Feedback ids are stable only if you keep
explicit `{#id}` tags (or keep headings unchanged).

## Building

```powershell
python build.py
python build.py --input my-specs --output dist
```

Edit `review.yaml` to set the project name and subtitle. The build prints a
short version hash; every feedback export records it so you know exactly what
was reviewed.

## Reviewing (what the stakeholder does)

- Click a feature in the left tree, pick a status, optionally comment.
- Statuses: Need this, Nice to have, Later, Don't need, Needs discussion.
- Keys: `1`-`5` set status, `j` / `k` move between features.
- "Show unresolved only" hides items that already have a status.
- "Mark remaining as no opinion" completes the review when they are done.
- Their name and feedback save in localStorage; closing the tab is safe.
- "Export feedback JSON" downloads `feedback-<name>-<date>.json`.
- "Copy JSON" is a fallback for locked-down machines.

## Collecting feedback

Ask each stakeholder to drop their `feedback-*.json` into `feedback/`, then:

```powershell
python collect.py
```

Outputs:

- `output/review-matrix.csv` - one row per feature, one decision/comment pair
  per reviewer. Opens in Excel (UTF-8 BOM included).
- `output/review-digest.md` - counts per reviewer plus comments grouped by
  decision, with Needs discussion first.

The feedback schema is intentionally small:

```json
{
  "schema": "spec-review-feedback-1",
  "reviewer": "Jane Smith",
  "project": "Case Management Platform",
  "reviewed_version": "1a2b3c4d5e",
  "counts": { "need": 3, "discussion": 1 },
  "feedback": {
    "routing.assignment": {
      "decision": "need",
      "comment": "This needs supervisor override."
    },
    "routing.escalation": {
      "decision": "discussion",
      "comment": "Need to confirm with operations."
    }
  },
  "unreviewed": ["reporting.dashboard"]
}
```

Decisions: `need`, `nice`, `later`, `no`, `discussion`, `no_opinion`.
No feedback means no opinion, not accepted.

## Files

```
build.py               markdown -> HTML + interchange JSON
collect.py             feedback JSONs -> CSV + digest
review_template.html   single-file UI (data injected at build time)
review.yaml            project name / subtitle
input/                 markdown specs (samples included)
feedback/              drop stakeholder feedback files here
samples/               example feedback for testing collect.py
output/                generated files
```

## Design constraints

Keep V1 boring: no server, no database, no auth, no React, no AI, no Strata
dependency. If the build needs Strata later, Strata can emit the same
`review-data.json` shape; this tool only cares about that interchange format.

# Spec_Review_Kit
