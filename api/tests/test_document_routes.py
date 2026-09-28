import ast
import unittest
from pathlib import Path


class DocumentRouteOrderingTests(unittest.TestCase):
    def test_search_is_declared_before_integer_document_route(self):
        router_file = Path(__file__).parents[1] / "app" / "routers" / "documents.py"
        module = ast.parse(router_file.read_text())
        get_paths = []

        for node in module.body:
            if not isinstance(node, ast.FunctionDef):
                continue
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "get"
                    and decorator.args
                    and isinstance(decorator.args[0], ast.Constant)
                ):
                    get_paths.append(decorator.args[0].value)

        self.assertLess(get_paths.index("/search"), get_paths.index("/{document_id}"))
        self.assertIn("/{document_id}", get_paths)


if __name__ == "__main__":
    unittest.main()
