from pathlib import Path


def test_catalog_queries_do_not_use_reserved_constraint_alias():
    text = Path("scripts/migrate.py").read_text("utf-8")
    assert "FROM pg_constraint constraint" not in text


def test_readback_does_not_pass_async_generators_to_all():
    import ast

    tree = ast.parse(Path("scripts/migrate.py").read_text("utf-8"))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in {"all", "any"}
        ):
            for arg in node.args:
                if isinstance(arg, ast.GeneratorExp):
                    assert not any(
                        isinstance(child, ast.Await) for child in ast.walk(arg)
                    )
