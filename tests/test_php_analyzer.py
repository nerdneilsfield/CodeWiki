from codewiki.src.be.dependency_analyzer.analyzers.php import analyze_php_file


PHP_SAMPLE = """<?php
namespace App;

use External\\Service as ExtService;

interface Runner {}

class Base {}

class Child extends Base implements Runner
{
    public function build(ExtService $service)
    {
        $obj = new ExtService();
        ExtService::boot();
    }
}
""".strip()


def test_php_analyzer_extracts_namespace_nodes(tmp_path):
    nodes, _ = analyze_php_file(str(tmp_path / "src" / "Child.php"), PHP_SAMPLE, str(tmp_path))

    names = {node.name for node in nodes}
    component_types = {node.name: node.component_type for node in nodes}

    assert {"Runner", "Base", "Child", "Child.build"} <= names
    assert component_types["Runner"] == "interface"
    assert component_types["Base"] == "class"
    assert component_types["Child.build"] == "method"


def test_php_analyzer_extracts_use_extends_implements_new_and_static_relationships(tmp_path):
    _, rels = analyze_php_file(str(tmp_path / "src" / "Child.php"), PHP_SAMPLE, str(tmp_path))

    pairs = {(rel.caller, rel.callee) for rel in rels}
    assert ("src.Child", "External.Service") in pairs or (
        "src.Child.php",
        "External.Service",
    ) in pairs
    assert ("App.Child", "App.Base") in pairs
    assert ("App.Child", "App.Runner") in pairs
    assert ("App.Child", "App.ExtService") in pairs or ("App.Child", "External.Service") in pairs


def test_php_analyzer_skips_template_files(tmp_path):
    nodes, rels = analyze_php_file(
        str(tmp_path / "resources" / "views" / "index.blade.php"),
        "<?php echo 'x';",
        str(tmp_path),
    )

    assert nodes == []
    assert rels == []
