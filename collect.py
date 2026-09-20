"""Merge stakeholder feedback JSON files into readable review outputs.

Reads every `*.json` feedback file from a folder, cross-references the build's
`output/review-data.json`, and writes:
- output/review-matrix.csv - one row per review item, one decision/comment
  column pair per reviewer (opens cleanly in Excel thanks to a UTF-8 BOM)
- output/review-digest.md - comments and discussion flags grouped for triage,
  with "needs discussion" first

Standard library only.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

DECISION_ORDER = ["discussion", "need", "nice", "later", "no", "no_opinion"]
DECISION_LABELS = {
    "discussion": "Needs discussion",
    "need": "Need this",
    "nice": "Nice to have",
    "later": "Later",
    "no": "Don't need",
    "no_opinion": "No opinion",
}
EXPECTED_DATA_SCHEMA = "spec-review-data-1"
EXPECTED_FEEDBACK_SCHEMA = "spec-review-feedback-1"
VALID_DECISIONS = set(DECISION_LABELS)


def resolve_path(path_text: str) -> Path:
    """Resolve a command-line path relative to this project folder."""
    path = Path(path_text)
    return path if path.is_absolute() else BASE_DIR / path


def load_review_data(path: Path) -> tuple[dict, list[dict]]:
    """Load the build interchange file and flatten it into reviewable rows.

    Returns the raw data plus rows in document order, each with the node id,
    area name, title, and full breadcrumb path.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("review data must be a JSON object")
    if data.get("schema") != EXPECTED_DATA_SCHEMA:
        raise ValueError(
            f"unsupported review data schema: {data.get('schema')!r} "
            f"(expected {EXPECTED_DATA_SCHEMA!r})"
        )
    rows: list[dict] = []

    def walk(nodes: list[dict], path_titles: list[str]) -> None:
        for node in nodes:
            titles = path_titles + [node.get("title") or node.get("id", "")]
            if node.get("reviewable"):
                rows.append(
                    {
                        "id": node.get("id", ""),
                        "area": titles[0] if len(titles) > 1 else "",
                        "title": node.get("title", ""),
                        "path": " / ".join(titles),
                    }
                )
            walk(node.get("children") or [], titles)

    walk(data.get("nodes") or [], [])
    return data, rows


def load_feedback(folder: Path) -> tuple[list[dict], list[str]]:
    """Load every feedback JSON in a folder, skipping unreadable files."""
    payloads: list[dict] = []
    warnings: list[str] = []
    for path in sorted(folder.glob("*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            warnings.append(f"{path.name}: skipped ({exc})")
            continue
        if not isinstance(payload, dict):
            warnings.append(f"{path.name}: skipped (not a JSON object)")
            continue
        if payload.get("schema") != EXPECTED_FEEDBACK_SCHEMA:
            warnings.append(
                f"{path.name}: skipped (unsupported schema {payload.get('schema')!r}; "
                f"expected {EXPECTED_FEEDBACK_SCHEMA!r})"
            )
            continue
        if not isinstance(payload.get("feedback"), dict):
            warnings.append(f"{path.name}: skipped ('feedback' must be a JSON object)")
            continue
        reviewer = str(payload.get("reviewer") or "").strip() or path.stem
        payload["_reviewer"] = reviewer
        payload["_file"] = path.name
        payloads.append(payload)
    return payloads, warnings


def validate_feedback(data: dict, payloads: list[dict]) -> list[str]:
    """Warn about version, project, and decision mismatches without losing data."""
    warnings: list[str] = []
    expected_project = str(data.get("project") or "")
    expected_version = str(data.get("version") or "")
    for payload in payloads:
        filename = payload["_file"]
        project = str(payload.get("project") or "")
        version = str(payload.get("reviewed_version") or "")
        if project != expected_project:
            warnings.append(
                f"{filename}: project {project or '(missing)'!r} does not match "
                f"{expected_project or '(missing)'!r}"
            )
        if version != expected_version:
            warnings.append(
                f"{filename}: reviewed version {version or '(missing)'!r} does not match "
                f"build version {expected_version or '(missing)'!r}"
            )
        for node_id, item in feedback_map(payload).items():
            decision = item["decision"]
            if decision and decision not in VALID_DECISIONS:
                warnings.append(f"{filename}: {node_id!r} has unrecognized decision {decision!r}")
    return warnings


def safe_csv_cell(value: object) -> str:
    """Prevent spreadsheet programs from interpreting user text as formulas."""
    text = str(value or "")
    if text.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + text
    return text


def markdown_text(value: object) -> str:
    """Keep user-provided text on one safe Markdown line."""
    return " ".join(str(value or "").splitlines()).replace("|", "\\|")


def reviewer_labels(payloads: list[dict]) -> list[str]:
    """Give each feedback file a unique column label, even with repeated names."""
    labels: list[str] = []
    seen: dict[str, int] = {}
    for payload in payloads:
        name = payload["_reviewer"]
        seen[name] = seen.get(name, 0) + 1
        labels.append(name if seen[name] == 1 else f"{name} ({seen[name]})")
    return labels


def feedback_map(payload: dict) -> dict:
    """Normalize a feedback payload into {node_id: {decision, comment}}."""
    raw = payload.get("feedback")
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict] = {}
    for node_id, item in raw.items():
        if isinstance(item, dict):
            normalized[str(node_id)] = {
                "decision": str(item.get("decision") or ""),
                "comment": str(item.get("comment") or ""),
            }
        else:
            normalized[str(node_id)] = {"decision": str(item or ""), "comment": ""}
    return normalized


def write_matrix(path: Path, rows: list[dict], payloads: list[dict], labels: list[str]) -> None:
    """Write the wide CSV matrix: one row per item, columns per reviewer."""
    known_ids = {row["id"] for row in rows}
    maps = [feedback_map(payload) for payload in payloads]

    unknown_ids: list[str] = []
    for payload_map in maps:
        for node_id in payload_map:
            if node_id not in known_ids and node_id not in unknown_ids:
                unknown_ids.append(node_id)

    header = ["feature_id", "area", "feature"]
    for label in labels:
        header.extend([f"{safe_csv_cell(label)} decision", f"{safe_csv_cell(label)} comment"])

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            line = [safe_csv_cell(row["id"]), safe_csv_cell(row["area"]), safe_csv_cell(row["title"])]
            for payload_map in maps:
                item = payload_map.get(row["id"], {})
                line.extend(
                    [
                        safe_csv_cell(DECISION_LABELS.get(item.get("decision", ""), item.get("decision", ""))),
                        safe_csv_cell(item.get("comment", "")),
                    ]
                )
            writer.writerow(line)
        for node_id in unknown_ids:
            line = [safe_csv_cell(node_id), "(not in this build)", ""]
            for payload_map in maps:
                item = payload_map.get(node_id, {})
                line.extend(
                    [
                        safe_csv_cell(DECISION_LABELS.get(item.get("decision", ""), item.get("decision", ""))),
                        safe_csv_cell(item.get("comment", "")),
                    ]
                )
            writer.writerow(line)


def write_digest(path: Path, data: dict, rows: list[dict], payloads: list[dict], labels: list[str]) -> None:
    """Write a markdown digest: counts, then entries grouped by decision."""
    row_by_id = {row["id"]: row for row in rows}
    known_ids = set(row_by_id)

    entries: list[dict] = []
    for payload, label in zip(payloads, labels):
        for node_id, item in feedback_map(payload).items():
            if not item["decision"] and not item["comment"].strip():
                continue
            entries.append(
                {
                    "id": node_id,
                    "label": label,
                    "decision": item["decision"],
                    "comment": item["comment"].strip(),
                    "path": row_by_id.get(node_id, {}).get("path", "(not in this build)"),
                }
            )

    versions = sorted({str(payload.get("reviewed_version") or "(unknown)") for payload in payloads})
    generated = dt.datetime.now().astimezone().isoformat(timespec="seconds")

    lines: list[str] = []
    lines.append(f"# Review digest - {data.get('project', 'Spec Review')}")
    lines.append("")
    lines.append(f"Generated: {generated}")
    lines.append("")
    lines.append(f"Reviewers: {', '.join(markdown_text(label) for label in labels) if labels else 'none'}")
    lines.append("")
    lines.append(f"Reviewed versions: {', '.join(versions)}")
    lines.append("")
    lines.append(f"Build version: {data.get('version', '(unknown)')}")
    lines.append("")

    lines.append("## Counts by reviewer")
    lines.append("")
    lines.append("| Reviewer | " + " | ".join(DECISION_LABELS[key] for key in DECISION_ORDER) + " |")
    lines.append("| --- | " + " | ".join("---" for _ in DECISION_ORDER) + " |")
    for payload, label in zip(payloads, labels):
        tally = {key: 0 for key in DECISION_ORDER}
        for item in feedback_map(payload).values():
            if item["decision"] in tally:
                tally[item["decision"]] += 1
        lines.append(f"| {markdown_text(label)} | " + " | ".join(str(tally[key]) for key in DECISION_ORDER) + " |")
    lines.append("")

    for key in DECISION_ORDER:
        grouped = [entry for entry in entries if entry["decision"] == key]
        if key == "discussion":
            show_all = grouped
        else:
            show_all = [entry for entry in grouped if entry["comment"]]
        if not show_all:
            continue
        lines.append(f"## {DECISION_LABELS[key]}")
        lines.append("")
        for entry in show_all:
            comment = markdown_text(entry["comment"]) if entry["comment"] else "(no comment)"
            lines.append(
                f"- **{markdown_text(entry['path'])}** (`{markdown_text(entry['id'])}`) - "
                f"{markdown_text(entry['label'])}: {comment}"
            )
        lines.append("")

    comment_only = [entry for entry in entries if not entry["decision"] and entry["comment"]]
    if comment_only:
        lines.append("## Comments without a status")
        lines.append("")
        for entry in comment_only:
            lines.append(
                f"- **{markdown_text(entry['path'])}** (`{markdown_text(entry['id'])}`) - "
                f"{markdown_text(entry['label'])}: {markdown_text(entry['comment'])}"
            )
        lines.append("")

    invalid = [entry for entry in entries if entry["decision"] and entry["decision"] not in VALID_DECISIONS]
    if invalid:
        lines.append("## Unrecognized statuses")
        lines.append("")
        for entry in invalid:
            lines.append(
                f"- **{markdown_text(entry['path'])}** (`{markdown_text(entry['id'])}`) - "
                f"{markdown_text(entry['label'])}: `{markdown_text(entry['decision'])}` "
                f"{markdown_text(entry['comment'])}"
            )
        lines.append("")

    unknown = [entry for entry in entries if entry["id"] not in known_ids]
    if unknown:
        lines.append("## Feedback for unknown ids")
        lines.append("")
        for entry in unknown:
            lines.append(
                f"- `{markdown_text(entry['id'])}` - {markdown_text(entry['label'])}: "
                f"{markdown_text(entry['decision'])} {markdown_text(entry['comment'])}"
            )
        lines.append("")

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, merge feedback files, and write the matrix and digest."""
    parser = argparse.ArgumentParser(description="Merge stakeholder feedback JSON files into review outputs.")
    parser.add_argument("--feedback", default="feedback", help="folder containing feedback JSON files")
    parser.add_argument("--data", default="output/review-data.json", help="build interchange file from build.py")
    parser.add_argument("--output", default="output", help="folder for generated reports")
    args = parser.parse_args(argv)

    feedback_dir = resolve_path(args.feedback)
    data_path = resolve_path(args.data)
    output_dir = resolve_path(args.output)

    if not data_path.is_file():
        print(f"error: review data not found: {data_path} (run build.py first)", file=sys.stderr)
        return 1
    if not feedback_dir.is_dir():
        print(f"error: feedback folder not found: {feedback_dir}", file=sys.stderr)
        return 1

    try:
        data, rows = load_review_data(data_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"error: could not read review data: {exc}", file=sys.stderr)
        return 1
    payloads, warnings = load_feedback(feedback_dir)
    warnings.extend(validate_feedback(data, payloads))
    for warning in warnings:
        print(f"warning: {warning}")
    if not payloads:
        print(f"error: no feedback JSON files found in {feedback_dir}", file=sys.stderr)
        return 1

    labels = reviewer_labels(payloads)
    matrix_path = output_dir / "review-matrix.csv"
    digest_path = output_dir / "review-digest.md"
    write_matrix(matrix_path, rows, payloads, labels)
    write_digest(digest_path, data, rows, payloads, labels)

    print(f"project:  {data.get('project', 'Spec Review')}")
    print(f"reviewers: {', '.join(labels)}")
    print(f"items:    {len(rows)}")
    print(f"wrote:    {matrix_path}")
    print(f"wrote:    {digest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
