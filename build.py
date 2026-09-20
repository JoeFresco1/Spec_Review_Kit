"""Compile stakeholder review bundles from markdown specs.

Build-time responsibilities:
- read markdown specs from an input folder
- parse a small deterministic heading convention into a feature tree
- write output/review-data.json (interchange format consumed by collect.py)
- inject that data into a self-contained HTML review file

This module uses only the Python standard library so the folder can be copied
anywhere and run without installing anything.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import html
import json
import re
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
EXPLICIT_ID_RE = re.compile(r"^(?P<title>.+?)\s*\{#(?P<id>[A-Za-z0-9._:-]+)\}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
INTERNAL_MARKER = "<!-- internal -->"
TEMPLATE_TOKEN = "__SPEC_DATA__"
DETAIL_HEADING_START = 4


def resolve_path(path_text: str) -> Path:
    """Resolve a command-line path relative to this project folder."""
    path = Path(path_text)
    return path if path.is_absolute() else BASE_DIR / path


def coerce_scalar(value: str) -> bool | int | str:
    """Convert a simple config value into a bool, int, or plain string."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    lowered = value.lower()
    if lowered in {"true", "yes"}:
        return True
    if lowered in {"false", "no"}:
        return False
    if re.fullmatch(r"-?\d+", value):
        return int(value)
    return value


def parse_simple_config(text: str) -> dict:
    """Parse flat `key: value` lines from front matter or review.yaml.

    Comments start with `#` and blank lines are ignored. No nested structures
    are supported by design; the build configuration is meant to stay tiny.
    """
    config: dict = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        config[key.strip()] = coerce_scalar(value.strip())
    return config


def split_front_matter(text: str) -> tuple[dict, str]:
    """Split optional `---` front matter from a markdown document."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            meta = parse_simple_config("\n".join(lines[1:index]))
            return meta, "\n".join(lines[index + 1:])
    return {}, text


def slugify(text: str) -> str:
    """Turn a heading or filename into a lowercase id fragment."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "node"


def title_from_filename(path: Path) -> str:
    """Derive a readable title from a file name like `spec-001-case-routing.md`."""
    stem = re.sub(r"^(?:spec[-_\s]+)?[0-9]+[-_\s]+", "", path.stem, flags=re.IGNORECASE)
    cleaned = re.sub(r"[-_]+", " ", stem).strip()
    return cleaned.title() if cleaned else path.stem


def title_and_id(raw_title: str) -> tuple[str, str | None]:
    """Return (title, explicit_id) from a heading that may end with `{#id}`."""
    match = EXPLICIT_ID_RE.match(raw_title)
    if match:
        return match.group("title").strip(), match.group("id")
    return raw_title.strip(), None


def extract_sections(lines: list[str]) -> tuple[list[str], list[dict]]:
    """Split markdown into a preamble and a flat list of heading sections.

    Code fences are tracked so `#` comments inside code blocks are not treated
    as headings. Each section keeps the raw lines that appear under it until
    the next heading.
    """
    preamble: list[str] = []
    sections: list[dict] = []
    current: dict | None = None
    fence: str | None = None

    for raw in lines:
        if fence:
            current["lines"].append(raw)
            if raw.strip().startswith(fence):
                fence = None
            continue
        fence_match = FENCE_RE.match(raw)
        if fence_match:
            fence = fence_match.group(1)
            (current["lines"] if current is not None else preamble).append(raw)
            continue
        heading = HEADING_RE.match(raw)
        if heading:
            title, explicit_id = title_and_id(heading.group(2))
            current = {
                "level": len(heading.group(1)),
                "title": title,
                "id": explicit_id,
                "lines": [],
                "children": [],
            }
            sections.append(current)
            continue
        if current is not None:
            current["lines"].append(raw)
        else:
            preamble.append(raw)
    return preamble, sections


def section_is_internal(section: dict) -> bool:
    """Return True when a section starts with the internal marker."""
    for line in section["lines"]:
        stripped = line.strip()
        if not stripped:
            continue
        return stripped == INTERNAL_MARKER
    return False


def drop_internal_sections(sections: list[dict]) -> list[dict]:
    """Remove internal sections and every section nested beneath them."""
    kept: list[dict] = []
    skip_level: int | None = None
    for section in sections:
        if skip_level is not None:
            if section["level"] > skip_level:
                continue
            skip_level = None
        if section_is_internal(section):
            skip_level = section["level"]
            continue
        kept.append(section)
    return kept


def nest_sections(sections: list[dict]) -> list[dict]:
    """Nest a flat heading list into a parent/child hierarchy by level."""
    roots: list[dict] = []
    stack: list[dict] = []
    for section in sections:
        while stack and stack[-1]["level"] >= section["level"]:
            stack.pop()
        if stack:
            stack[-1]["children"].append(section)
        else:
            roots.append(section)
        stack.append(section)
    return roots


def render_inline(text: str) -> str:
    """Render a tiny inline markdown subset: code, bold, italics, links."""
    pieces = re.split(r"(`[^`]+`)", text)
    rendered: list[str] = []
    for piece in pieces:
        if len(piece) >= 2 and piece.startswith("`") and piece.endswith("`"):
            rendered.append(f"<code>{html.escape(piece[1:-1])}</code>")
            continue
        escaped = html.escape(piece)
        escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
        escaped = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", escaped)
        escaped = re.sub(
            r"\[([^\]]+)\]\((https?://[^)\s]+)\)",
            r'<a href="\2" target="_blank" rel="noopener">\1</a>',
            escaped,
        )
        rendered.append(escaped)
    return "".join(rendered)


def render_blocks(lines: list[str]) -> str:
    """Render a markdown subset to HTML: paragraphs, lists, quotes, code.

    Only the block types stakeholders actually see in specs are supported.
    Nested lists and tables intentionally fall through as plain text.
    """
    out: list[str] = []
    paragraph: list[str] = []
    items: list[str] = []
    list_tag: str | None = None
    fence: str | None = None
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            out.append(f"<p>{render_inline(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_list() -> None:
        nonlocal list_tag
        if items:
            body = "".join(f"<li>{render_inline(item)}</li>" for item in items)
            out.append(f"<{list_tag}>{body}</{list_tag}>")
            items.clear()
        list_tag = None

    for raw in lines:
        stripped = raw.strip()
        if fence:
            if stripped.startswith(fence):
                out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
                code_lines.clear()
                fence = None
            else:
                code_lines.append(raw.rstrip())
            continue
        fence_match = FENCE_RE.match(raw)
        if fence_match:
            flush_paragraph()
            flush_list()
            fence = fence_match.group(1)
            continue
        if not stripped:
            flush_paragraph()
            flush_list()
            continue
        heading = re.match(r"^(#{4,6})\s+(.+?)\s*$", stripped)
        if heading:
            flush_paragraph()
            flush_list()
            level = min(len(heading.group(1)), 6)
            out.append(f"<h{level}>{render_inline(heading.group(2))}</h{level}>")
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            flush_paragraph()
            if list_tag != "ul":
                flush_list()
                list_tag = "ul"
            items.append(stripped[2:].strip())
            continue
        ordered = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if ordered:
            flush_paragraph()
            if list_tag != "ol":
                flush_list()
                list_tag = "ol"
            items.append(ordered.group(1).strip())
            continue
        if stripped.startswith(">"):
            flush_paragraph()
            flush_list()
            out.append(f"<blockquote>{render_inline(stripped.lstrip('> ').strip())}</blockquote>")
            continue
        paragraph.append(stripped)

    flush_paragraph()
    flush_list()
    if fence and code_lines:
        out.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "".join(out)


def render_children(section: dict, heading_level: int) -> str:
    """Render nested subsections of a feature as progressively smaller headings."""
    parts: list[str] = []
    for child in section.get("children", []):
        level = min(heading_level, 6)
        parts.append(f"<h{level}>{render_inline(child['title'])}</h{level}>")
        parts.append(render_blocks(child["lines"]))
        parts.append(render_children(child, heading_level + 1))
    return "".join(parts)


def plain_summary(lines: list[str], limit: int = 240) -> str:
    """Extract a short plain-text summary from a section's first content block.

    Falls back to the first bullet when a section leads with a list. Markdown
    markers are stripped so the result can be shown directly in the UI.
    """
    collected: list[str] = []
    for raw in lines:
        stripped = raw.strip()
        if not stripped:
            if collected:
                break
            continue
        if stripped.startswith("#") or FENCE_RE.match(raw) or stripped.startswith("<!--"):
            continue
        if stripped.startswith(("- ", "* ", "+ ")):
            if collected:
                break
            collected.append(stripped[2:].strip())
            break
        collected.append(stripped)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", " ".join(collected))
    text = re.sub(r"[`*_]", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > limit:
        text = text[: limit - 1].rstrip() + "..."
    return text


def unique_id(base: str, used_ids: set[str], warnings: list[str]) -> str:
    """Return a document-unique node id, recording collisions as warnings."""
    candidate = base
    suffix = 2
    while candidate in used_ids:
        candidate = f"{base}-{suffix}"
        suffix += 1
    if candidate != base:
        warnings.append(f"duplicate id '{base}' renamed to '{candidate}'")
    used_ids.add(candidate)
    return candidate


def build_feature(section: dict, area_id: str, used_ids: set[str], warnings: list[str]) -> dict:
    """Convert an H2 section into a reviewable feature node."""
    base = section.get("id") or f"{area_id}.{slugify(section['title'])}"
    details = render_blocks(section["lines"]) + render_children(section, DETAIL_HEADING_START)
    return {
        "id": unique_id(base, used_ids, warnings),
        "title": section["title"],
        "kind": "feature",
        "reviewable": True,
        "summary": plain_summary(section["lines"]),
        "details_html": details,
        "children": [],
    }


def build_area(section: dict, used_ids: set[str], warnings: list[str]) -> dict:
    """Convert an H1 section into an area node with feature children.

    When an area has no H2 children it becomes reviewable itself so single-page
    specs still produce feedback targets. H3+ sections always fold into the
    details HTML rather than becoming separate nodes.
    """
    area_id = unique_id(section.get("id") or slugify(section["title"]), used_ids, warnings)
    features = [child for child in section["children"] if child["level"] == 2]
    extras = [child for child in section["children"] if child["level"] != 2]

    if features:
        children = [build_feature(child, area_id, used_ids, warnings) for child in features]
        details = ""
        if extras:
            details = render_blocks(section["lines"]) + render_children({"children": extras}, DETAIL_HEADING_START)
        return {
            "id": area_id,
            "title": section["title"],
            "kind": "area",
            "reviewable": False,
            "summary": plain_summary(section["lines"]),
            "details_html": details,
            "children": children,
        }

    details = render_blocks(section["lines"]) + render_children(section, DETAIL_HEADING_START)
    return {
        "id": area_id,
        "title": section["title"],
        "kind": "area",
        "reviewable": True,
        "summary": plain_summary(section["lines"]),
        "details_html": details,
        "children": [],
    }


def build_document(input_dir: Path, project: str, subtitle: str) -> tuple[dict, list[str]]:
    """Build the full review data structure from every markdown file in a folder."""
    warnings: list[str] = []
    used_ids: set[str] = set()
    areas: list[tuple[int, dict]] = []

    sources = sorted(input_dir.glob("*.md"))
    if not sources:
        raise FileNotFoundError(f"No markdown files found in {input_dir}")

    for path in sources:
        text = path.read_text(encoding="utf-8")
        meta, body = split_front_matter(text)
        if meta.get("stakeholder") is False:
            continue
        preamble, flat_sections = extract_sections(body.splitlines())
        roots = nest_sections(drop_internal_sections(flat_sections))

        h1_roots = [section for section in roots if section["level"] == 1]
        if not h1_roots:
            synthetic = {
                "level": 1,
                "title": meta.get("title") or title_from_filename(path),
                "id": meta.get("id"),
                "lines": preamble,
                "children": roots,
            }
            h1_roots = [synthetic]
        elif "".join(preamble).strip():
            warnings.append(f"{path.name}: content before the first heading is ignored")

        order = meta.get("order", 100000)
        for root in h1_roots:
            areas.append((order, build_area(root, used_ids, warnings)))

    areas.sort(key=lambda item: (item[0], item[1]["title"].lower()))
    nodes = [area for _, area in areas]
    data = {
        "schema": "spec-review-data-1",
        "project": project,
        "subtitle": subtitle,
        "generated_at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        "version": compute_version(project, nodes),
        "nodes": nodes,
    }
    return data, warnings


def compute_version(project: str, nodes: list[dict]) -> str:
    """Hash the review content so feedback can be tied to an exact build."""
    payload = json.dumps(
        {"project": project, "nodes": nodes},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:10]


def write_bundle(data: dict, output_dir: Path, template_path: Path) -> tuple[Path, Path]:
    """Write review-data.json and the self-contained stakeholder HTML file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    data_path = output_dir / "review-data.json"
    data_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    template = template_path.read_text(encoding="utf-8")
    if TEMPLATE_TOKEN not in template:
        raise ValueError(f"Template {template_path} is missing the {TEMPLATE_TOKEN} token")
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    html_path = output_dir / "stakeholder-review.html"
    html_path.write_text(template.replace(TEMPLATE_TOKEN, payload), encoding="utf-8")
    return data_path, html_path


def count_reviewable(nodes: list[dict]) -> int:
    """Count every node a stakeholder is expected to give a decision on."""
    total = 0
    for node in nodes:
        if node.get("reviewable"):
            total += 1
        total += count_reviewable(node.get("children") or [])
    return total


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, compile the specs, and report what was produced."""
    parser = argparse.ArgumentParser(description="Build the stakeholder review HTML from markdown specs.")
    parser.add_argument("--input", default="input", help="folder containing markdown specs")
    parser.add_argument("--output", default="output", help="folder for generated files")
    parser.add_argument("--template", default="review_template.html", help="HTML template with the data token")
    parser.add_argument("--config", default="review.yaml", help="optional build configuration")
    args = parser.parse_args(argv)

    input_dir = resolve_path(args.input)
    output_dir = resolve_path(args.output)
    template_path = resolve_path(args.template)
    config_path = resolve_path(args.config)

    if not input_dir.is_dir():
        print(f"error: input folder not found: {input_dir}", file=sys.stderr)
        return 1
    if not template_path.is_file():
        print(f"error: template not found: {template_path}", file=sys.stderr)
        return 1

    config = parse_simple_config(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    project = str(config.get("project") or "Spec Review")
    subtitle = str(config.get("subtitle") or "")

    try:
        data, warnings = build_document(input_dir, project, subtitle)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    data_path, html_path = write_bundle(data, output_dir, template_path)

    for warning in warnings:
        print(f"warning: {warning}")
    print(f"project:      {data['project']}")
    print(f"version:      {data['version']}")
    print(f"review items: {count_reviewable(data['nodes'])}")
    print(f"wrote:        {data_path}")
    print(f"wrote:        {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
