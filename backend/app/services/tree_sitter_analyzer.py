from pathlib import Path

from tree_sitter_language_pack import get_parser


class TreeSitterAnalyzer:
    def analyze(
        self,
        file_path: str,
        language: str,
    ) -> dict:
        source_code = Path(file_path).read_text(
            encoding="utf-8"
        )

        parser = get_parser(language)

        tree = parser.parse(
            source_code.encode("utf-8")
        )

        if language in {"javascript", "typescript"}:
            return self._analyze_javascript_typescript(
                tree.root_node,
                source_code,
                file_path,
                language,
            )

        return {
            "file": file_path,
            "language": language,
            "functions": [],
            "classes": [],
            "imports": [],
        }

    def _analyze_javascript_typescript(
        self,
        root_node,
        source_code: str,
        file_path: str,
        language: str,
    ) -> dict:
        functions = []
        classes = []
        imports = []

        self._walk_node(
            root_node,
            source_code,
            functions,
            classes,
            imports,
        )

        return {
            "file": file_path,
            "language": language,
            "functions": functions,
            "classes": classes,
            "imports": imports,
        }

    def _walk_node(
        self,
        node,
        source_code: str,
        functions: list,
        classes: list,
        imports: list,
    ):
        if node.type == "import_statement":
            imports.append(
                self._node_text(
                    node,
                    source_code,
                )
            )

        elif node.type == "function_declaration":
            functions.append(
                self._analyze_function(
                    node,
                    source_code,
                )
            )

        elif node.type == "class_declaration":
            classes.append(
                self._analyze_class(
                    node,
                    source_code,
                )
            )

            return

        for child in node.children:
            self._walk_node(
                child,
                source_code,
                functions,
                classes,
                imports,
            )

    def _analyze_function(
        self,
        node,
        source_code: str,
    ) -> dict:
        name_node = node.child_by_field_name("name")

        name = (
            self._node_text(
                name_node,
                source_code,
            )
            if name_node
            else "<anonymous>"
        )

        calls = self._find_calls(
            node,
            source_code,
        )

        parameters_node = node.child_by_field_name(
            "parameters"
        )

        parameters = []

        if parameters_node:
            parameters = [
                self._node_text(
                    child,
                    source_code,
                )
                for child in parameters_node.children
                if child.type not in {
                    "(",
                    ")",
                    ",",
                }
            ]

        return {
            "name": name,
            "line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "parameters": parameters,
            "decorators": [],
            "calls": calls,
            "async": self._node_text(
                node,
                source_code,
            ).lstrip().startswith("async "),
        }

    def _analyze_class(
        self,
        node,
        source_code: str,
    ) -> dict:
        name_node = node.child_by_field_name("name")

        name = (
            self._node_text(
                name_node,
                source_code,
            )
            if name_node
            else "<anonymous>"
        )

        methods = []

        body = node.child_by_field_name("body")

        if body:
            for child in body.named_children:
                if child.type == "method_definition":
                    methods.append(
                        self._analyze_method(
                            child,
                            source_code,
                        )
                    )

        return {
            "name": name,
            "line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "bases": [],
            "decorators": [],
            "methods": methods,
        }

    def _analyze_method(
        self,
        node,
        source_code: str,
    ) -> dict:
        name_node = node.child_by_field_name("name")

        name = (
            self._node_text(
                name_node,
                source_code,
            )
            if name_node
            else "<anonymous>"
        )

        parameters_node = node.child_by_field_name(
            "parameters"
        )

        parameters = []

        if parameters_node:
            parameters = [
                self._node_text(
                    child,
                    source_code,
                )
                for child in parameters_node.children
                if child.type not in {
                    "(",
                    ")",
                    ",",
                }
            ]

        return {
            "name": name,
            "line": node.start_point[0] + 1,
            "end_line": node.end_point[0] + 1,
            "parameters": parameters,
            "decorators": [],
            "calls": self._find_calls(
                node,
                source_code,
            ),
            "async": self._node_text(
                node,
                source_code,
            ).lstrip().startswith("async "),
        }

    def _find_calls(
        self,
        node,
        source_code: str,
    ) -> list[str]:
        calls = []

        for descendant in self._descendants(node):
            if descendant.type == "call_expression":
                function_node = descendant.child_by_field_name(
                    "function"
                )

                if not function_node:
                    continue

                call_name = self._extract_call_name(
                    function_node,
                    source_code,
                )

                if call_name:
                    calls.append(call_name)

            elif descendant.type == "new_expression":
                constructor_node = descendant.child_by_field_name(
                    "constructor"
                )

                if not constructor_node:
                    continue

                call_name = self._extract_call_name(
                    constructor_node,
                    source_code,
                )

                if call_name:
                    calls.append(
                        f"new {call_name}"
                    )

        return calls

    def _extract_call_name(
        self,
        node,
        source_code: str,
    ) -> str | None:
        """
        Extract a clean callable name from a Tree-sitter
        function/member-expression node.

        Examples:

            foo()
                -> foo

            foo.bar()
                -> foo.bar

            z.string()
                -> z.string

            z.string().min(1)
                -> min

            object({
                ...
            })
                -> object

            new User()
                -> User

        We intentionally avoid returning the entire
        expression when it contains nested calls or
        complex arguments.
        """

        if node.type == "identifier":
            return self._node_text(
                node,
                source_code,
            )

        if node.type == "this":
            return "this"

        if node.type == "member_expression":
            object_node = node.child_by_field_name(
                "object"
            )

            property_node = node.child_by_field_name(
                "property"
            )

            if not property_node:
                return None

            property_name = self._node_text(
                property_node,
                source_code,
            )

            if not object_node:
                return property_name

            if object_node.type in {
                "identifier",
                "this",
                "member_expression",
            }:
                object_name = self._extract_call_name(
                    object_node,
                    source_code,
                )

                if object_name:
                    return (
                        f"{object_name}.{property_name}"
                    )

            # Example:
            #
            # z.string().min(1)
            #
            # The object is itself a call_expression.
            # Returning the complete expression would
            # create huge database values.
            #
            # We therefore keep only the final method.
            if object_node.type == "call_expression":
                return property_name

            return property_name

        if node.type == "optional_chain_expression":
            for child in node.named_children:
                result = self._extract_call_name(
                    child,
                    source_code,
                )

                if result:
                    return result

            return None

        # Do NOT store complex expressions such as:
        #
        # foo({
        #     very: "large",
        #     object: ...
        # })
        #
        # or:
        #
        # something().another().method(...)
        #
        # as one enormous database string.
        return None

    def _descendants(self, node):
        for child in node.children:
            yield child
            yield from self._descendants(child)

    @staticmethod
    def _node_text(
        node,
        source_code: str,
    ) -> str:
        return source_code[
            node.start_byte:node.end_byte
        ]