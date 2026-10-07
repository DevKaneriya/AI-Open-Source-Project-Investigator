import ast
from pathlib import Path


class CodeAnalyzer:

    def analyze_python_file(self, file_path: str) -> dict:
        path = Path(file_path)

        source_code = path.read_text(
            encoding="utf-8"
        )

        tree = ast.parse(source_code)

        functions = []
        classes = []
        imports = []

        for node in ast.walk(tree):

            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                functions.append(
                    self._analyze_function(node)
                )

            elif isinstance(node, ast.ClassDef):
                classes.append(
                    self._analyze_class(node)
                )

            elif isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)

            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.append(node.module)

        return {
            "file": str(path),
            "functions": functions,
            "classes": classes,
            "imports": imports,
        }

    def _analyze_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
    ) -> dict:

        calls = []

        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                call_name = self._get_call_name(child)
                if call_name:
                    calls.append(call_name)

        parameters = [
            argument.arg
            for argument in node.args.args
        ]

        decorators = [
            self._get_decorator_name(decorator)
            for decorator in node.decorator_list
        ]

        return {
            "name": node.name,
            "line": node.lineno,
            "end_line": getattr(
                node,
                "end_lineno",
                node.lineno,
            ),
            "parameters": parameters,
            "decorators": decorators,
            "calls": calls,
            "async": isinstance(
                node,
                ast.AsyncFunctionDef,
            ),
        }

    def _analyze_class(
        self,
        node: ast.ClassDef,
    ) -> dict:

        methods = []

        for child in node.body:
            if isinstance(
                child,
                (ast.FunctionDef, ast.AsyncFunctionDef),
            ):
                methods.append(
                    self._analyze_function(child)
                )

        bases = [
            self._get_call_name(base)
            for base in node.bases
        ]

        decorators = [
            self._get_decorator_name(decorator)
            for decorator in node.decorator_list
        ]

        return {
            "name": node.name,
            "line": node.lineno,
            "end_line": getattr(
                node,
                "end_lineno",
                node.lineno,
            ),
            "bases": bases,
            "decorators": decorators,
            "methods": methods,
        }

    @staticmethod
    def _get_call_name(node: ast.AST) -> str | None:

        if isinstance(node, ast.Name):
            return node.id

        if isinstance(node, ast.Attribute):
            parts = []

            current = node

            while isinstance(current, ast.Attribute):
                parts.append(current.attr)
                current = current.value

            if isinstance(current, ast.Name):
                parts.append(current.id)

            return ".".join(reversed(parts))

        return None

    @staticmethod
    def _get_decorator_name(node: ast.AST) -> str:

        name = CodeAnalyzer._get_call_name(node)

        if name:
            return name

        return ast.unparse(node)