from pathlib import Path

import re



from tree_sitter_language_pack import get_parser





class TreeSitterAnalyzer:

    """

    Structural analyzer for JavaScript / TypeScript repositories.



    Responsibilities:

    - Parse source code with Tree-sitter.

    - Extract clean symbols.

    - Extract classes and methods.

    - Extract imports.

    - Extract function/method calls.

    - Preserve accurate line numbers.

    - Never store malformed source fragments as symbol names.

    """



    SUPPORTED_LANGUAGES = {

        "javascript",

        "typescript",

    }



    VALID_IDENTIFIER_PATTERN = re.compile(
        r"^[A-Za-z_$][A-Za-z0-9_$]*$"
    )



    def analyze(

        self,

        file_path: str,

        language: str,

    ) -> dict:

        source_code = Path(file_path).read_text(

            encoding="utf-8"

        )



        if language not in self.SUPPORTED_LANGUAGES:

            return {

                "file": file_path,

                "language": language,

                "functions": [],

                "classes": [],

                "imports": [],

            }



        parser = get_parser(language)



        # Tree-sitter byte offsets are based on UTF-8 bytes.

        source_bytes = source_code.encode("utf-8")



        tree = parser.parse(source_bytes)



        return self._analyze_javascript_typescript(

            tree.root_node,

            source_code,

            source_bytes,

            file_path,

            language,

        )



    # ------------------------------------------------------------------

    # Main JS / TS analysis

    # ------------------------------------------------------------------



    def _analyze_javascript_typescript(

        self,

        root_node,

        source_code: str,

        source_bytes: bytes,

        file_path: str,

        language: str,

    ) -> dict:

        functions = []

        classes = []

        imports = []



        self._walk_node(

            root_node,

            source_code,

            source_bytes,

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



    # ------------------------------------------------------------------

    # AST traversal

    # ------------------------------------------------------------------



    def _walk_node(

        self,

        node,

        source_code: str,

        source_bytes: bytes,

        functions: list,

        classes: list,

        imports: list,

    ):

        node_type = node.type



        # --------------------------------------------------------------

        # Imports

        # --------------------------------------------------------------



        if node_type == "import_statement":

            import_text = self._node_text(

                node,

                source_bytes,

            ).strip()



            if import_text:

                imports.append(import_text)



        # --------------------------------------------------------------

        # Normal function declarations

        # --------------------------------------------------------------



        elif node_type in {

            "function_declaration",

            "generator_function_declaration",

        }:

            functions.append(

                self._analyze_function(

                    node,

                    source_bytes,

                )

            )



        # --------------------------------------------------------------

        # Classes

        # --------------------------------------------------------------



        elif node_type == "class_declaration":

            classes.append(

                self._analyze_class(

                    node,

                    source_bytes,

                )

            )



        # --------------------------------------------------------------

        # Interfaces

        # --------------------------------------------------------------



        elif node_type == "interface_declaration":

            classes.append(

                self._analyze_interface(

                    node,

                    source_bytes,

                )

            )



        # --------------------------------------------------------------

        # Type aliases

        # --------------------------------------------------------------



        elif node_type == "type_alias_declaration":

            functions.append(

                self._analyze_type_alias(

                    node,

                    source_bytes,

                )

            )



        # --------------------------------------------------------------

        # Enums

        # --------------------------------------------------------------



        elif node_type == "enum_declaration":

            classes.append(

                self._analyze_enum(

                    node,

                    source_bytes,

                )

            )



        # --------------------------------------------------------------

        # Arrow functions / function expressions assigned to variables

        # --------------------------------------------------------------



        elif node_type == "variable_declarator":

            arrow_function = self._find_function_value(node)



            if arrow_function is not None:

                symbol_name = self._extract_variable_name(

                    node,

                    source_bytes,

                )



                if symbol_name:

                    functions.append(

                        self._analyze_arrow_function(

                            node,

                            arrow_function,

                            symbol_name,

                            source_bytes,

                        )

                    )



        # Continue traversal.



        # IMPORTANT:

        # We intentionally do NOT return after classes/interfaces/etc.

        # Their children still need to be visited so nested structures

        # and imports are not missed.

        for child in node.named_children:

            self._walk_node(

                child,

                source_code,

                source_bytes,

                functions,

                classes,

                imports,

            )



    # ------------------------------------------------------------------

    # Function analysis

    # ------------------------------------------------------------------



    def _analyze_function(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._clean_symbol_name(

            self._node_text(name_node, source_bytes)

            if name_node

            else None

        )



        if not name:

            name = "<anonymous>"



        parameters = self._extract_parameters(

            node,

            source_bytes,

        )



        calls = self._find_calls(

            node,

            source_bytes,

        )



        source = self._node_text(

            node,

            source_bytes,

        )



        return {

            "name": name,

                        "symbol_type": "function",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "parameters": parameters,

            "decorators": [],

            "calls": calls,

            "async": source.lstrip().startswith("async "),

        }



    # ------------------------------------------------------------------

    # Arrow function analysis

    # ------------------------------------------------------------------



    def _analyze_arrow_function(

        self,

        variable_node,

        arrow_node,

        symbol_name: str,

        source_bytes: bytes,

    ) -> dict:

        parameters = self._extract_parameters_from_arrow(

            arrow_node,

            source_bytes,

        )



        calls = self._find_calls(

            arrow_node,

            source_bytes,

        )



        source = self._node_text(

            arrow_node,

            source_bytes,

        )



        return {

            "name": symbol_name,

                        "symbol_type": "function",

"line": variable_node.start_point[0] + 1,

            "end_line": variable_node.end_point[0] + 1,

            "parameters": parameters,

            "decorators": [],

            "calls": calls,

            "async": source.lstrip().startswith("async "),

        }



    # ------------------------------------------------------------------

    # Class analysis

    # ------------------------------------------------------------------



    def _analyze_class(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._clean_symbol_name(

            self._node_text(name_node, source_bytes)

            if name_node

            else None

        )



        if not name:

            name = "<anonymous>"



        methods = []



        body = node.child_by_field_name("body")



        if body:

            for child in body.named_children:

                if child.type == "method_definition":

                    methods.append(

                        self._analyze_method(

                            child,

                            source_bytes,

                        )

                    )



        return {

            "name": name,

                        "symbol_type": "class",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "bases": self._extract_class_bases(

                node,

                source_bytes,

            ),

            "decorators": [],

            "methods": methods,

        }



    # ------------------------------------------------------------------

    # Interface analysis

    # ------------------------------------------------------------------



    def _analyze_interface(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._clean_symbol_name(

            self._node_text(name_node, source_bytes)

            if name_node

            else None

        )



        if not name:

            name = "<anonymous>"



        methods = []



        body = node.child_by_field_name("body")



        if body:

            for child in body.named_children:

                if child.type in {

                    "method_signature",

                    "abstract_method_signature",

                }:

                    methods.append(

                        self._analyze_method_signature(

                            child,

                            source_bytes,

                        )

                    )



        return {

            "name": name,

                        "symbol_type": "interface",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "bases": [],

            "decorators": [],

            "methods": methods,

        }



    # ------------------------------------------------------------------

    # Type alias analysis

    # ------------------------------------------------------------------



    def _analyze_type_alias(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._clean_symbol_name(

            self._node_text(name_node, source_bytes)

            if name_node

            else None

        )



        if not name:

            name = "<anonymous>"



        return {

            "name": name,

                        "symbol_type": "type",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "bases": [],

            "decorators": [],

            "methods": [],

        }



    # ------------------------------------------------------------------

    # Enum analysis

    # ------------------------------------------------------------------



    def _analyze_enum(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._clean_symbol_name(

            self._node_text(name_node, source_bytes)

            if name_node

            else None

        )



        if not name:

            name = "<anonymous>"



        return {

            "name": name,

                        "symbol_type": "enum",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "bases": [],

            "decorators": [],

            "methods": [],

        }



    # ------------------------------------------------------------------

    # Method analysis

    # ------------------------------------------------------------------



    def _analyze_method(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._extract_property_name(

            name_node,

            source_bytes,

        )



        if not name:

            name = "<anonymous>"



        parameters = self._extract_parameters(

            node,

            source_bytes,

        )



        calls = self._find_calls(

            node,

            source_bytes,

        )



        source = self._node_text(

            node,

            source_bytes,

        )



        return {

            "name": name,

                        "symbol_type": "method",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "parameters": parameters,

            "decorators": [],

            "calls": calls,

            "async": source.lstrip().startswith("async "),

        }



    # ------------------------------------------------------------------

    # Interface method analysis

    # ------------------------------------------------------------------



    def _analyze_method_signature(

        self,

        node,

        source_bytes: bytes,

    ) -> dict:

        name_node = node.child_by_field_name("name")



        name = self._extract_property_name(

            name_node,

            source_bytes,

        )



        if not name:

            name = "<anonymous>"



        return {

            "name": name,

                        "symbol_type": "method",

"line": node.start_point[0] + 1,

            "end_line": node.end_point[0] + 1,

            "parameters": self._extract_parameters(

                node,

                source_bytes,

            ),

            "decorators": [],

            "calls": [],

            "async": False,

        }



    # ------------------------------------------------------------------

    # Parameters

    # ------------------------------------------------------------------



    def _extract_parameters(

        self,

        node,

        source_bytes: bytes,

    ) -> list[str]:

        parameters_node = node.child_by_field_name(

            "parameters"

        )



        if not parameters_node:

            return []



        parameters = []



        for child in parameters_node.named_children:

            text = self._node_text(

                child,

                source_bytes,

            ).strip()



            if text:

                parameters.append(text)



        return parameters



    def _extract_parameters_from_arrow(

        self,

        node,

        source_bytes: bytes,

    ) -> list[str]:

        parameters_node = node.child_by_field_name(

            "parameters"

        )



        if parameters_node:

            return self._extract_parameters(

                node,

                source_bytes,

            )



        # Single parameter arrow function:

        #

        # value => value.id

        #

        # Tree-sitter may expose this as a "parameter" field

        # depending on grammar version.



        parameter_node = node.child_by_field_name(

            "parameter"

        )



        if parameter_node:

            text = self._node_text(

                parameter_node,

                source_bytes,

            ).strip()



            return [text] if text else []



        return []



    # ------------------------------------------------------------------

    # Variable / arrow function helpers

    # ------------------------------------------------------------------



    def _find_function_value(self, variable_node):

        value_node = variable_node.child_by_field_name(

            "value"

        )



        if not value_node:

            return None



        if value_node.type in {

            "arrow_function",

            "function",

            "function_expression",

            "generator_function",

        }:

            return value_node



        return None



    def _extract_variable_name(

        self,

        variable_node,

        source_bytes: bytes,

    ) -> str | None:

        name_node = variable_node.child_by_field_name(

            "name"

        )



        if not name_node:

            return None



        return self._clean_symbol_name(

            self._node_text(

                name_node,

                source_bytes,

            )

        )



    # ------------------------------------------------------------------

    # Class inheritance

    # ------------------------------------------------------------------



    def _extract_class_bases(

        self,

        node,

        source_bytes: bytes,

    ) -> list[str]:

        bases = []



        heritage = node.child_by_field_name(

            "heritage"

        )



        if not heritage:

            return bases



        for child in heritage.named_children:

            text = self._node_text(

                child,

                source_bytes,

            ).strip()



            if text:

                bases.append(text)



        return bases



    # ------------------------------------------------------------------

    # Call extraction

    # ------------------------------------------------------------------



    def _find_calls(

        self,

        node,

        source_bytes: bytes,

    ) -> list[str]:

        calls = []

        seen = set()



        for descendant in self._descendants(node):

            # ----------------------------------------------------------

            # foo()

            # foo.bar()

            # z.string()

            # ----------------------------------------------------------



            if descendant.type == "call_expression":

                function_node = descendant.child_by_field_name(

                    "function"

                )



                if not function_node:

                    continue



                call_name = self._extract_call_name(

                    function_node,

                    source_bytes,

                )



                if call_name and call_name not in seen:

                    calls.append(call_name)

                    seen.add(call_name)



            # ----------------------------------------------------------

            # new User()

            # new Date()

            # ----------------------------------------------------------



            elif descendant.type == "new_expression":

                constructor_node = descendant.child_by_field_name(

                    "constructor"

                )



                if not constructor_node:

                    continue



                call_name = self._extract_call_name(

                    constructor_node,

                    source_bytes,

                )



                if call_name:

                    call_name = f"new {call_name}"



                    if call_name not in seen:

                        calls.append(call_name)

                        seen.add(call_name)



        return calls



    def _extract_call_name(

        self,

        node,

        source_bytes: bytes,

    ) -> str | None:

        """

        Extract a clean callable name.



        Examples:



            foo()

                -> foo



            foo.bar()

                -> foo.bar



            z.string()

                -> z.string



            z.string().min(1)

                -> min



            object({...})

                -> object



            new User()

                -> User



        Never return the complete call expression because that can

        contain large argument objects and create bad DB values.

        """



        if not node:

            return None



        node_type = node.type



        # --------------------------------------------------------------

        # Simple identifier

        # --------------------------------------------------------------



        if node_type in {

            "identifier",

            "property_identifier",

            "private_property_identifier",

        }:

            value = self._node_text(

                node,

                source_bytes,

            ).strip()



            return self._clean_call_name(value)



        # --------------------------------------------------------------

        # this

        # --------------------------------------------------------------



        if node_type == "this":

            return "this"



        # --------------------------------------------------------------

        # Member expression

        #

        # object.property

        # this.property

        # z.string

        # --------------------------------------------------------------



        if node_type in {

            "member_expression",

            "optional_member_expression",

        }:

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

                source_bytes,

            ).strip()



            property_name = self._clean_call_name(

                property_name

            )



            if not property_name:

                return None



            if not object_node:

                return property_name



            # Nested call:

            #

            # z.string().min()

            #

            # We don't want:

            # z.string().min

            #

            # because the actual callable being invoked is "min".



            if object_node.type in {

                "call_expression",

                "new_expression",

                "await_expression",

            }:

                return property_name



            object_name = self._extract_call_name(

                object_node,

                source_bytes,

            )



            if object_name:

                return f"{object_name}.{property_name}"



            return property_name



        # --------------------------------------------------------------

        # Optional chain

        # --------------------------------------------------------------



        if node_type in {

            "optional_chain_expression",

        }:

            for child in node.named_children:

                result = self._extract_call_name(

                    child,

                    source_bytes,

                )



                if result:

                    return result



            return None



        # --------------------------------------------------------------

        # Parenthesized expression

        # --------------------------------------------------------------



        if node_type == "parenthesized_expression":

            inner = node.named_children



            if len(inner) == 1:

                return self._extract_call_name(

                    inner[0],

                    source_bytes,

                )



            return None



        # --------------------------------------------------------------

        # Do not store arbitrary expressions.

        # --------------------------------------------------------------



        return None



    # ------------------------------------------------------------------

    # Tree traversal

    # ------------------------------------------------------------------



    def _descendants(self, node):

        for child in node.named_children:

            yield child

            yield from self._descendants(child)



    # ------------------------------------------------------------------

    # Symbol validation

    # ------------------------------------------------------------------



    def _clean_symbol_name(

        self,

        value: str | None,

    ) -> str | None:

        if not value:

            return None



        value = value.strip()



        if not value:

            return None



        # Remove TypeScript private-field marker.

        if value.startswith("#"):

            value = value[1:]



        # Only accept actual identifier-like names.

        #

        # This prevents corrupted values such as:

        #

        #   "keResource(i"

        #   "yResult(result: P"

        #   "s.ring()"

        #   "o.ject({"

        #

        # from entering PostgreSQL.

        if not self.VALID_IDENTIFIER_PATTERN.fullmatch(

            value

        ):

            return None



        return value



    def _clean_call_name(

        self,

        value: str | None,

    ) -> str | None:

        if not value:

            return None



        value = value.strip()



        if not value:

            return None



        # A simple identifier.

        if self.VALID_IDENTIFIER_PATTERN.fullmatch(value):

            return value



        # A dotted member path:

        #

        # foo.bar

        # this.foo

        # z.string

        #

        parts = value.split(".")



        if all(

            self.VALID_IDENTIFIER_PATTERN.fullmatch(part)

            for part in parts

        ):

            return value



        return None



    # ------------------------------------------------------------------

    # Property names

    # ------------------------------------------------------------------



    def _extract_property_name(

        self,

        node,

        source_bytes: bytes,

    ) -> str | None:

        if not node:

            return None



        text = self._node_text(

            node,

            source_bytes,

        ).strip()



        if text.startswith("#"):

            text = text[1:]



        return self._clean_symbol_name(text)



    # ------------------------------------------------------------------

    # Safe Tree-sitter text extraction

    # ------------------------------------------------------------------



    @staticmethod

    def _node_text(

        node,

        source_bytes: bytes,

    ) -> str:

        """

        Tree-sitter positions are UTF-8 byte offsets.



        Therefore we MUST slice bytes first and decode afterwards.



        Incorrect:



            source_code[node.start_byte:node.end_byte]



        Correct:



            source_bytes[node.start_byte:node.end_byte].decode(...)

        """



        if not node:

            return ""



        return source_bytes[

            node.start_byte:node.end_byte

        ].decode(

            "utf-8",

            errors="replace",

        )