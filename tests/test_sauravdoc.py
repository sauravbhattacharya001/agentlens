"""Tests for sauravdoc -- documentation generator for sauravcode."""

import json
import os
import sys
import tempfile
import shutil
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from sauravdoc import (
    parse_source, parse_file, render_markdown, render_json,
    find_srv_files, generate_index, Parameter, FunctionDoc, EnumDoc,
    ClassDoc, ModuleDoc, _collect_comment_block, _parse_params, _collect_body,
)


class TestParseParams:
    def test_empty(self):
        assert _parse_params("") == []

    def test_simple(self):
        params = _parse_params("x y z")
        assert [p.name for p in params] == ["x", "y", "z"]

    def test_type_annotated(self):
        params = _parse_params("x: int y: string")
        assert params[0].name == "x"
        assert params[0].type_hint == "int"
        assert params[1].name == "y"
        assert params[1].type_hint == "string"

    def test_mixed(self):
        params = _parse_params("a b: float c")
        assert params[0].type_hint is None
        assert params[1].type_hint == "float"
        assert params[2].type_hint is None


class TestCollectCommentBlock:
    def test_no_comments(self):
        lines = ["x = 1", "function foo x"]
        assert _collect_comment_block(lines, 0) == ""

    def test_single_comment(self):
        lines = ["# Add two numbers", "function add x y"]
        assert _collect_comment_block(lines, 0) == "Add two numbers"

    def test_multi_line_comment(self):
        lines = ["# First line", "# Second line", "function foo"]
        result = _collect_comment_block(lines, 1)
        assert "First line" in result
        assert "Second line" in result

    def test_stops_at_non_comment(self):
        lines = ["x = 1", "# doc", "function foo"]
        assert _collect_comment_block(lines, 1) == "doc"


class TestCollectBody:
    def test_indented_body(self):
        lines = ["    return x + y", "    print x", "next_thing"]
        body = _collect_body(lines, 0, 0)
        assert len(body) == 2

    def test_empty_lines_in_body(self):
        lines = ["    x = 1", "", "    y = 2", "done"]
        body = _collect_body(lines, 0, 0)
        assert len(body) == 3

    def test_no_body(self):
        lines = ["next_thing"]
        body = _collect_body(lines, 0, 0)
        assert len(body) == 0


class TestParseFunctions:
    def test_basic_function(self):
        src = "function add x y\n    return x + y\n"
        doc = parse_source(src)
        assert len(doc.functions) == 1
        assert doc.functions[0].name == "add"
        assert len(doc.functions[0].params) == 2

    def test_function_with_comment(self):
        src = "# Adds two numbers\nfunction add x y\n    return x + y\n"
        doc = parse_source(src)
        assert doc.functions[0].doc_comment == "Adds two numbers"

    def test_multiline_comment(self):
        src = "# Line one\n# Line two\nfunction foo\n    print 1\n"
        doc = parse_source(src)
        assert "Line one" in doc.functions[0].doc_comment
        assert "Line two" in doc.functions[0].doc_comment

    def test_no_params(self):
        src = "function greet\n    print \"hello\"\n"
        doc = parse_source(src)
        assert doc.functions[0].params == []

    def test_private_function(self):
        src = "function _helper x\n    return x\n"
        doc = parse_source(src)
        assert doc.functions[0].is_private is True

    def test_multiple_functions(self):
        src = "function add x y\n    return x + y\n\nfunction sub x y\n    return x - y\n"
        doc = parse_source(src)
        assert len(doc.functions) == 2

    def test_nested_body(self):
        src = "function max a b\n    if a > b\n        return a\n    return b\n"
        doc = parse_source(src)
        assert len(doc.functions[0].source_lines) >= 3

    def test_return_type_inferred(self):
        src = "function id x\n    return x\n"
        doc = parse_source(src)
        assert doc.functions[0].return_type == "inferred"

    def test_no_return(self):
        src = "function say_hi\n    print \"hi\"\n"
        doc = parse_source(src)
        assert doc.functions[0].return_type is None

    def test_line_number(self):
        src = "\n\nfunction foo\n    print 1\n"
        doc = parse_source(src)
        assert doc.functions[0].line_number == 3


class TestParseEnums:
    def test_basic_enum(self):
        src = "enum Color\n    Red\n    Green\n    Blue\n"
        doc = parse_source(src)
        assert len(doc.enums) == 1
        assert doc.enums[0].name == "Color"
        assert len(doc.enums[0].variants) == 3

    def test_enum_with_comment(self):
        src = "# Primary colors\nenum Color\n    Red\n    Green\n    Blue\n"
        doc = parse_source(src)
        assert doc.enums[0].doc_comment == "Primary colors"

    def test_enum_variants_with_values(self):
        src = "enum Status\n    Active 1\n    Inactive 0\n"
        doc = parse_source(src)
        assert "Active 1" in doc.enums[0].variants

    def test_enum_skips_comments_in_body(self):
        src = "enum Dir\n    # cardinal\n    North\n    South\n"
        doc = parse_source(src)
        assert len(doc.enums[0].variants) == 2


class TestParseClasses:
    def test_class_with_methods(self):
        src = "class Counter\n    function init\n        count = 0\n    function increment\n        count = count + 1\n"
        doc = parse_source(src)
        assert len(doc.classes) == 1
        assert len(doc.classes[0].methods) == 2

    def test_class_with_comment(self):
        src = "# A simple counter\nclass Counter\n    function tick\n        print 1\n"
        doc = parse_source(src)
        assert doc.classes[0].doc_comment == "A simple counter"

    def test_method_comments(self):
        src = "class Calc\n    # Add values\n    function add x y\n        return x + y\n"
        doc = parse_source(src)
        assert doc.classes[0].methods[0].doc_comment == "Add values"


class TestParseImports:
    def test_import(self):
        src = 'import "utils.srv"\n'
        doc = parse_source(src)
        assert doc.imports == ["utils.srv"]

    def test_multiple_imports(self):
        src = 'import "a.srv"\nimport "b.srv"\n'
        doc = parse_source(src)
        assert len(doc.imports) == 2


class TestParseGlobalVars:
    def test_simple_assignment(self):
        src = "x = 10\ny = 20\n"
        doc = parse_source(src)
        assert "x" in doc.global_vars
        assert "y" in doc.global_vars

    def test_function_not_counted(self):
        src = "function foo\n    return 1\n"
        doc = parse_source(src)
        assert doc.global_vars == []


class TestModuleComment:
    def test_module_comment(self):
        src = "# This is a module\n# for math stuff\n\nx = 1\n"
        doc = parse_source(src)
        assert "This is a module" in doc.module_comment

    def test_decoration_stripped(self):
        src = "# ========\n# Title\n# ========\n\nx = 1\n"
        doc = parse_source(src)
        assert "====" not in doc.module_comment
        assert "Title" in doc.module_comment


class TestRenderMarkdown:
    def _make_doc(self):
        return ModuleDoc(
            filename="test.srv",
            title="Test",
            module_comment="A test module",
            functions=[
                FunctionDoc(
                    name="add",
                    params=[Parameter("x"), Parameter("y")],
                    doc_comment="Add two numbers",
                    line_number=5,
                    source_lines=["function add x y", "    return x + y"],
                )
            ],
            enums=[
                EnumDoc(name="Color", variants=["Red", "Green"], doc_comment="Colors", line_number=1)
            ],
        )

    def test_title(self):
        assert "# Test" in render_markdown(self._make_doc())

    def test_module_comment(self):
        assert "A test module" in render_markdown(self._make_doc())

    def test_function_signature(self):
        assert "`add(x, y)`" in render_markdown(self._make_doc())

    def test_function_doc(self):
        assert "Add two numbers" in render_markdown(self._make_doc())

    def test_enum_rendered(self):
        md = render_markdown(self._make_doc())
        assert "`Color`" in md
        assert "Red" in md

    def test_no_source(self):
        assert "```srv" not in render_markdown(self._make_doc(), include_source=False)

    def test_with_source(self):
        assert "```srv" in render_markdown(self._make_doc(), include_source=True)

    def test_private_excluded(self):
        doc = ModuleDoc(filename="t.srv", functions=[FunctionDoc(name="_helper", is_private=True, line_number=1)])
        assert "_helper" not in render_markdown(doc, include_private=False)

    def test_private_included(self):
        doc = ModuleDoc(filename="t.srv", functions=[FunctionDoc(name="_helper", is_private=True, line_number=1)])
        assert "_helper" in render_markdown(doc, include_private=True)

    def test_summary_mode(self):
        md = render_markdown(self._make_doc(), summary_only=True)
        assert "| Function |" in md
        assert "| `add`" in md

    def test_toc(self):
        assert "Table of Contents" in render_markdown(self._make_doc())

    def test_footer(self):
        assert "Generated by sauravdoc" in render_markdown(self._make_doc())

    def test_line_number(self):
        assert "line 5" in render_markdown(self._make_doc())

    def test_type_hint_in_signature(self):
        doc = ModuleDoc(filename="t.srv", functions=[
            FunctionDoc(name="typed", params=[Parameter("x", type_hint="int")], line_number=1)
        ])
        assert "x: int" in render_markdown(doc)

    def test_parameters_section(self):
        assert "**Parameters:**" in render_markdown(self._make_doc())

    def test_class_rendering(self):
        doc = ModuleDoc(filename="t.srv", classes=[
            ClassDoc(name="Foo", methods=[FunctionDoc(name="bar", line_number=2)], doc_comment="A class", line_number=1)
        ])
        md = render_markdown(doc)
        assert "`Foo`" in md
        assert "Foo.bar" in md

    def test_imports_rendered(self):
        doc = ModuleDoc(filename="t.srv", imports=["utils.srv"])
        assert "`utils.srv`" in render_markdown(doc)

    def test_variables_rendered(self):
        doc = ModuleDoc(filename="t.srv", global_vars=["x", "y"])
        assert "`x`" in render_markdown(doc)

    def test_decorator_in_signature(self):
        doc = ModuleDoc(filename="t.srv", functions=[
            FunctionDoc(name="f", decorators=["memoize"], line_number=1)
        ])
        assert "@memoize" in render_markdown(doc)


class TestRenderJson:
    def test_valid_json(self):
        doc = ModuleDoc(filename="t.srv", functions=[FunctionDoc(name="foo", line_number=1)])
        result = json.loads(render_json(doc))
        assert result["filename"] == "t.srv"

    def test_function_in_json(self):
        doc = ModuleDoc(filename="t.srv", functions=[
            FunctionDoc(name="bar", params=[Parameter("x")], line_number=1)
        ])
        result = json.loads(render_json(doc))
        assert result["functions"][0]["name"] == "bar"

    def test_private_excluded_json(self):
        doc = ModuleDoc(filename="t.srv", functions=[
            FunctionDoc(name="_priv", is_private=True, line_number=1),
            FunctionDoc(name="pub", line_number=2),
        ])
        result = json.loads(render_json(doc, include_private=False))
        names = [f["name"] for f in result["functions"]]
        assert "_priv" not in names
        assert "pub" in names

    def test_private_included_json(self):
        doc = ModuleDoc(filename="t.srv", functions=[
            FunctionDoc(name="_priv", is_private=True, line_number=1),
        ])
        result = json.loads(render_json(doc, include_private=True))
        assert result["functions"][0]["name"] == "_priv"


class TestFileOperations:
    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()

    def teardown_method(self):
        shutil.rmtree(self.tmpdir)

    def _write(self, name, content):
        path = os.path.join(self.tmpdir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(content)
        return path

    def test_find_srv_files_single(self):
        p = self._write("a.srv", "x = 1")
        assert find_srv_files(p) == [p]

    def test_find_srv_files_dir(self):
        self._write("a.srv", "x = 1")
        self._write("b.srv", "y = 2")
        self._write("c.txt", "not srv")
        assert len(find_srv_files(self.tmpdir)) == 2

    def test_find_srv_recursive(self):
        self._write("a.srv", "x = 1")
        self._write(os.path.join("sub", "b.srv"), "y = 2")
        assert len(find_srv_files(self.tmpdir, recursive=True)) == 2

    def test_find_no_files(self):
        assert find_srv_files(os.path.join(self.tmpdir, "nope.srv")) == []

    def test_parse_file(self):
        p = self._write("test.srv", "function foo x\n    return x\n")
        doc = parse_file(p)
        assert doc.functions[0].name == "foo"


class TestGenerateIndex:
    def test_index_header(self):
        docs = [ModuleDoc(filename="a.srv"), ModuleDoc(filename="b.srv")]
        idx = generate_index(docs)
        assert "Module Index" in idx
        assert "2 module(s)" in idx

    def test_index_links(self):
        docs = [ModuleDoc(filename="math.srv", functions=[FunctionDoc(name="add", line_number=1)])]
        idx = generate_index(docs)
        assert "math.srv" in idx
        assert "| 1 |" in idx

    def test_totals(self):
        docs = [
            ModuleDoc(filename="a.srv", functions=[FunctionDoc(name="f", line_number=1)]),
            ModuleDoc(filename="b.srv", enums=[EnumDoc(name="E", line_number=1)]),
        ]
        idx = generate_index(docs)
        assert "1 functions" in idx
        assert "1 enums" in idx


class TestIntegration:
    def test_full_file(self):
        src = """# ==========================================
# Math utilities
# ==========================================

import "helpers.srv"

PI = 3.14159

# Add two numbers together
function add x y
    return x + y

# Subtract b from a
function sub a b
    return a - b

# Direction enum
enum Direction
    North
    South
    East
    West

# A simple counter class
class Counter
    # Initialize counter
    function init
        count = 0
    # Increment by one
    function increment
        count = count + 1
"""
        doc = parse_source(src, "math.srv")
        assert doc.module_comment == "Math utilities"
        assert len(doc.functions) == 2
        assert len(doc.enums) == 1
        assert len(doc.classes) == 1
        assert len(doc.classes[0].methods) == 2
        assert "helpers.srv" in doc.imports
        assert "PI" in doc.global_vars

    def test_roundtrip_md(self):
        src = "# Doc\nfunction foo x\n    return x\n"
        doc = parse_source(src)
        md = render_markdown(doc)
        assert "foo" in md
        assert "Doc" in md

    def test_roundtrip_json(self):
        src = "function bar a b\n    return a + b\n"
        doc = parse_source(src)
        j = json.loads(render_json(doc))
        assert j["functions"][0]["name"] == "bar"
        assert len(j["functions"][0]["params"]) == 2

    def test_empty_file(self):
        doc = parse_source("")
        assert doc.functions == []
        assert doc.enums == []

    def test_only_comments(self):
        doc = parse_source("# just a comment\n# another one\n")
        assert doc.module_comment != ""
        assert doc.functions == []
