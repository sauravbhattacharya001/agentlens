#!/usr/bin/env python3
"""
sauravdoc -- Documentation generator for sauravcode (.srv) files.

Parses .srv source files and extracts functions, enums, classes, and
module-level comments to produce Markdown or JSON documentation.

Usage:
    python sauravdoc.py <file_or_dir> [options]

Options:
    --format md|json     Output format (default: md)
    --output <path>      Write to file instead of stdout
    --title <title>      Document title (default: filename)
    --recursive          Scan directories recursively
    --private            Include items starting with underscore
    --no-source          Omit source code snippets
    --summary            One-line-per-item summary table only
"""

import os
import re
import sys
import json
import argparse
from dataclasses import dataclass, field, asdict
from typing import List, Optional, Dict


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class Parameter:
    """A function parameter."""
    name: str
    type_hint: Optional[str] = None
    default: Optional[str] = None


@dataclass
class FunctionDoc:
    """Documentation for a single function."""
    name: str
    params: List[Parameter] = field(default_factory=list)
    doc_comment: str = ""
    line_number: int = 0
    source_lines: List[str] = field(default_factory=list)
    return_type: Optional[str] = None
    decorators: List[str] = field(default_factory=list)
    is_private: bool = False


@dataclass
class EnumDoc:
    """Documentation for an enum."""
    name: str
    variants: List[str] = field(default_factory=list)
    doc_comment: str = ""
    line_number: int = 0
    source_lines: List[str] = field(default_factory=list)


@dataclass
class ClassDoc:
    """Documentation for a class."""
    name: str
    methods: List[FunctionDoc] = field(default_factory=list)
    doc_comment: str = ""
    line_number: int = 0
    source_lines: List[str] = field(default_factory=list)


@dataclass
class ModuleDoc:
    """Documentation for an entire .srv file."""
    filename: str
    title: str = ""
    module_comment: str = ""
    functions: List[FunctionDoc] = field(default_factory=list)
    enums: List[EnumDoc] = field(default_factory=list)
    classes: List[ClassDoc] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    global_vars: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Parser helpers
# ---------------------------------------------------------------------------

_COMMENT_RE = re.compile(r'^\s*#\s?(.*)')
_FUNC_RE = re.compile(r'^(\s*)function\s+(\w+)\s*(.*)')
_ENUM_RE = re.compile(r'^(\s*)enum\s+(\w+)\s*')
_CLASS_RE = re.compile(r'^(\s*)class\s+(\w+)\s*')
_IMPORT_RE = re.compile(r'^import\s+"([^"]+)"')
_ASSIGN_RE = re.compile(r'^([a-zA-Z_]\w*)\s*=\s*(.+)')
_DECORATOR_RE = re.compile(r'^@(\w+)')


def _collect_comment_block(lines: List[str], end_index: int) -> str:
    """Walk backward from *end_index* collecting contiguous comment lines."""
    comments = []
    i = end_index
    while i >= 0:
        m = _COMMENT_RE.match(lines[i])
        if m:
            comments.append(m.group(1))
            i -= 1
        else:
            break
    comments.reverse()
    return "\n".join(comments).strip()


def _collect_body(lines: List[str], start: int, base_indent: int) -> List[str]:
    """Collect indented body lines starting from *start*."""
    body = []
    for i in range(start, len(lines)):
        line = lines[i]
        if line.strip() == "":
            body.append(line)
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if indent > base_indent:
            body.append(line)
        else:
            break
    while body and body[-1].strip() == "":
        body.pop()
    return body


def _parse_params(param_str: str) -> List[Parameter]:
    """Parse space-separated parameter names, with optional : type annotations."""
    params = []
    if not param_str.strip():
        return params
    tokens = param_str.strip().split()
    i = 0
    while i < len(tokens):
        name = tokens[i]
        type_hint = None
        if name.endswith(":") and i + 1 < len(tokens):
            name = name[:-1]
            type_hint = tokens[i + 1]
            i += 2
        elif i + 1 < len(tokens) and tokens[i + 1] == ":":
            if i + 2 < len(tokens):
                type_hint = tokens[i + 2]
                i += 3
            else:
                i += 2
        else:
            i += 1
        params.append(Parameter(name=name, type_hint=type_hint))
    return params


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------

def parse_file(filepath: str) -> ModuleDoc:
    """Parse a .srv file and extract documentation."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return parse_source(content, os.path.basename(filepath))


def parse_source(source: str, filename: str = "<source>") -> ModuleDoc:
    """Parse sauravcode source string and extract documentation."""
    lines = source.split("\n")
    doc = ModuleDoc(filename=filename, title=os.path.splitext(filename)[0])

    # Extract module-level comment (leading comment block)
    module_comments = []
    for line in lines:
        m = _COMMENT_RE.match(line)
        if m:
            module_comments.append(m.group(1))
        elif line.strip() == "":
            if module_comments:
                module_comments.append("")
            continue
        else:
            break
    # Trim decoration lines (e.g., "====")
    cleaned = []
    for c in module_comments:
        if c and all(ch in "=-~#*" for ch in c.strip()):
            continue
        cleaned.append(c)
    doc.module_comment = "\n".join(cleaned).strip()

    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # Decorators (skip, handled when we hit a function)
        dec_m = _DECORATOR_RE.match(stripped)
        if dec_m:
            i += 1
            continue

        # Functions
        func_m = _FUNC_RE.match(line)
        if func_m:
            indent = len(func_m.group(1))
            name = func_m.group(2)
            param_str = func_m.group(3)
            comment = _collect_comment_block(lines, i - 1)

            # Collect decorators above comment block
            decorators = []
            dec_start = i - 1
            while dec_start >= 0 and _COMMENT_RE.match(lines[dec_start]):
                dec_start -= 1
            while dec_start >= 0:
                dm = _DECORATOR_RE.match(lines[dec_start].strip())
                if dm:
                    decorators.insert(0, dm.group(1))
                    dec_start -= 1
                else:
                    break

            body = _collect_body(lines, i + 1, indent)
            source_lines = [line] + body

            return_type = None
            for bl in body:
                if "return " in bl:
                    return_type = "inferred"
                    break

            fd = FunctionDoc(
                name=name,
                params=_parse_params(param_str),
                doc_comment=comment,
                line_number=i + 1,
                source_lines=source_lines,
                return_type=return_type,
                decorators=decorators,
                is_private=name.startswith("_"),
            )
            doc.functions.append(fd)
            i += 1 + len(body)
            continue

        # Enums
        enum_m = _ENUM_RE.match(line)
        if enum_m:
            indent = len(enum_m.group(1))
            name = enum_m.group(2)
            comment = _collect_comment_block(lines, i - 1)
            body = _collect_body(lines, i + 1, indent)
            variants = []
            for bl in body:
                v = bl.strip()
                if v and not v.startswith("#"):
                    variants.append(v)
            ed = EnumDoc(
                name=name,
                variants=variants,
                doc_comment=comment,
                line_number=i + 1,
                source_lines=[line] + body,
            )
            doc.enums.append(ed)
            i += 1 + len(body)
            continue

        # Class
        class_m = _CLASS_RE.match(line)
        if class_m:
            indent = len(class_m.group(1))
            name = class_m.group(2)
            comment = _collect_comment_block(lines, i - 1)
            body = _collect_body(lines, i + 1, indent)
            methods = []
            j = 0
            while j < len(body):
                mfunc = _FUNC_RE.match(body[j])
                if mfunc:
                    mname = mfunc.group(2)
                    mparam_str = mfunc.group(3)
                    mcomment = _collect_comment_block(body, j - 1)
                    mindent = len(mfunc.group(1))
                    mbody = _collect_body(body, j + 1, mindent)
                    methods.append(FunctionDoc(
                        name=mname,
                        params=_parse_params(mparam_str),
                        doc_comment=mcomment,
                        line_number=i + 1 + j + 1,
                        source_lines=[body[j]] + mbody,
                        is_private=mname.startswith("_"),
                    ))
                    j += 1 + len(mbody)
                else:
                    j += 1
            cd = ClassDoc(
                name=name,
                methods=methods,
                doc_comment=comment,
                line_number=i + 1,
                source_lines=[line] + body,
            )
            doc.classes.append(cd)
            i += 1 + len(body)
            continue

        # Imports
        import_m = _IMPORT_RE.match(stripped)
        if import_m:
            doc.imports.append(import_m.group(1))
            i += 1
            continue

        # Top-level assignments
        assign_m = _ASSIGN_RE.match(stripped)
        if assign_m and not any(stripped.startswith(kw) for kw in ("if ", "else", "while ", "for ")):
            doc.global_vars.append(assign_m.group(1))
            i += 1
            continue

        i += 1

    return doc


# ---------------------------------------------------------------------------
# Renderers
# ---------------------------------------------------------------------------

def render_markdown(doc: ModuleDoc, include_source: bool = True,
                    include_private: bool = False, summary_only: bool = False) -> str:
    """Render ModuleDoc as Markdown."""
    lines = []
    title = doc.title or doc.filename
    lines.append(f"# {title}")
    lines.append("")

    if doc.module_comment:
        lines.append(doc.module_comment)
        lines.append("")

    # Table of contents
    sections = []
    if doc.imports:
        sections.append("Imports")
    if doc.enums:
        sections.append("Enums")
    if doc.classes:
        sections.append("Classes")
    funcs = [f for f in doc.functions if include_private or not f.is_private]
    if funcs:
        sections.append("Functions")
    if doc.global_vars:
        sections.append("Variables")

    if sections:
        lines.append("## Table of Contents")
        lines.append("")
        for s in sections:
            anchor = s.lower().replace(" ", "-")
            lines.append(f"- [{s}](#{anchor})")
        lines.append("")

    # Summary table mode
    if summary_only and funcs:
        lines.append("## Functions")
        lines.append("")
        lines.append("| Function | Parameters | Description |")
        lines.append("|----------|------------|-------------|")
        for f in funcs:
            params = ", ".join(p.name for p in f.params)
            desc = f.doc_comment.split("\n")[0] if f.doc_comment else ""
            lines.append(f"| `{f.name}` | `{params}` | {desc} |")
        lines.append("")
        return "\n".join(lines)

    # Imports
    if doc.imports:
        lines.append("## Imports")
        lines.append("")
        for imp in doc.imports:
            lines.append(f"- `{imp}`")
        lines.append("")

    # Enums
    if doc.enums:
        lines.append("## Enums")
        lines.append("")
        for enum in doc.enums:
            lines.append(f"### `{enum.name}`")
            lines.append("")
            if enum.doc_comment:
                lines.append(enum.doc_comment)
                lines.append("")
            lines.append(f"*Defined at line {enum.line_number}*")
            lines.append("")
            if enum.variants:
                lines.append("**Variants:**")
                lines.append("")
                for v in enum.variants:
                    lines.append(f"- `{v}`")
                lines.append("")
            if include_source and enum.source_lines:
                lines.append("<details><summary>Source</summary>")
                lines.append("")
                lines.append("```srv")
                lines.extend(enum.source_lines)
                lines.append("```")
                lines.append("</details>")
                lines.append("")

    # Classes
    if doc.classes:
        lines.append("## Classes")
        lines.append("")
        for cls in doc.classes:
            lines.append(f"### `{cls.name}`")
            lines.append("")
            if cls.doc_comment:
                lines.append(cls.doc_comment)
                lines.append("")
            lines.append(f"*Defined at line {cls.line_number}*")
            lines.append("")
            methods = [m for m in cls.methods if include_private or not m.is_private]
            if methods:
                lines.append("**Methods:**")
                lines.append("")
                for m in methods:
                    params = ", ".join(p.name for p in m.params)
                    lines.append(f"#### `{cls.name}.{m.name}({params})`")
                    lines.append("")
                    if m.doc_comment:
                        lines.append(m.doc_comment)
                        lines.append("")
                    if include_source and m.source_lines:
                        lines.append("<details><summary>Source</summary>")
                        lines.append("")
                        lines.append("```srv")
                        lines.extend(m.source_lines)
                        lines.append("```")
                        lines.append("</details>")
                        lines.append("")

    # Functions
    if funcs:
        lines.append("## Functions")
        lines.append("")
        for f in funcs:
            params = ", ".join(
                f"{p.name}: {p.type_hint}" if p.type_hint else p.name
                for p in f.params
            )
            sig = f"`{f.name}({params})`"
            if f.decorators:
                dec_str = " ".join(f"@{d}" for d in f.decorators)
                lines.append(f"### {dec_str} {sig}")
            else:
                lines.append(f"### {sig}")
            lines.append("")
            if f.doc_comment:
                lines.append(f.doc_comment)
                lines.append("")
            lines.append(f"*Defined at line {f.line_number}*")
            lines.append("")
            if f.params:
                lines.append("**Parameters:**")
                lines.append("")
                for p in f.params:
                    type_str = f" *({p.type_hint})*" if p.type_hint else ""
                    lines.append(f"- `{p.name}`{type_str}")
                lines.append("")
            if include_source and f.source_lines:
                lines.append("<details><summary>Source</summary>")
                lines.append("")
                lines.append("```srv")
                lines.extend(f.source_lines)
                lines.append("```")
                lines.append("</details>")
                lines.append("")

    # Variables
    if doc.global_vars:
        lines.append("## Variables")
        lines.append("")
        for v in doc.global_vars:
            lines.append(f"- `{v}`")
        lines.append("")

    # Footer
    lines.append("---")
    lines.append(f"*Generated by sauravdoc from `{doc.filename}`*")
    lines.append("")

    return "\n".join(lines)


def render_json(doc: ModuleDoc, include_private: bool = False) -> str:
    """Render ModuleDoc as JSON."""
    d = asdict(doc)
    if not include_private:
        d["functions"] = [f for f in d["functions"] if not f["is_private"]]
        for cls in d["classes"]:
            cls["methods"] = [m for m in cls["methods"] if not m["is_private"]]
    return json.dumps(d, indent=2)


# ---------------------------------------------------------------------------
# Multi-file / directory support
# ---------------------------------------------------------------------------

def find_srv_files(path: str, recursive: bool = False) -> List[str]:
    """Find all .srv files in a path."""
    if os.path.isfile(path):
        return [path] if path.endswith(".srv") else []
    if not os.path.isdir(path):
        return []
    files = []
    if recursive:
        for root, _dirs, filenames in os.walk(path):
            for fn in sorted(filenames):
                if fn.endswith(".srv"):
                    files.append(os.path.join(root, fn))
    else:
        for fn in sorted(os.listdir(path)):
            fp = os.path.join(path, fn)
            if os.path.isfile(fp) and fn.endswith(".srv"):
                files.append(fp)
    return files


def generate_index(docs: List[ModuleDoc]) -> str:
    """Generate a Markdown index page for multiple modules."""
    lines = ["# Module Index", ""]
    lines.append(f"*{len(docs)} module(s) documented*")
    lines.append("")
    lines.append("| Module | Functions | Enums | Classes | Description |")
    lines.append("|--------|-----------|-------|---------|-------------|")
    for d in docs:
        desc = d.module_comment.split("\n")[0] if d.module_comment else ""
        lines.append(
            f"| [{d.filename}]({d.filename.replace('.srv', '.md')}) "
            f"| {len(d.functions)} | {len(d.enums)} | {len(d.classes)} "
            f"| {desc} |"
        )
    lines.append("")
    total_funcs = sum(len(d.functions) for d in docs)
    total_enums = sum(len(d.enums) for d in docs)
    total_classes = sum(len(d.classes) for d in docs)
    lines.append(f"**Totals:** {total_funcs} functions, {total_enums} enums, {total_classes} classes")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        prog="sauravdoc",
        description="Documentation generator for sauravcode (.srv) files.",
    )
    parser.add_argument("path", help="Source file or directory")
    parser.add_argument("--format", choices=["md", "json"], default="md",
                        help="Output format (default: md)")
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--title", help="Document title")
    parser.add_argument("--recursive", "-r", action="store_true",
                        help="Scan directories recursively")
    parser.add_argument("--private", action="store_true",
                        help="Include private items (starting with _)")
    parser.add_argument("--no-source", action="store_true",
                        help="Omit source code snippets")
    parser.add_argument("--summary", action="store_true",
                        help="Summary table only")

    args = parser.parse_args()

    files = find_srv_files(args.path, args.recursive)
    if not files:
        print(f"Error: No .srv files found at '{args.path}'", file=sys.stderr)
        sys.exit(1)

    docs = []
    for fp in files:
        doc = parse_file(fp)
        if args.title and len(files) == 1:
            doc.title = args.title
        docs.append(doc)

    if args.format == "json":
        if len(docs) == 1:
            output = render_json(docs[0], include_private=args.private)
        else:
            output = json.dumps(
                [json.loads(render_json(d, include_private=args.private)) for d in docs],
                indent=2,
            )
    else:
        if len(docs) == 1:
            output = render_markdown(
                docs[0],
                include_source=not args.no_source,
                include_private=args.private,
                summary_only=args.summary,
            )
        else:
            parts = [generate_index(docs), ""]
            for d in docs:
                parts.append(render_markdown(
                    d,
                    include_source=not args.no_source,
                    include_private=args.private,
                    summary_only=args.summary,
                ))
            output = "\n".join(parts)

    if args.output:
        os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"Documentation written to {args.output}")
    else:
        print(output)


if __name__ == "__main__":
    main()
