"""
Deterministic regression tests for retrieval targeting, question-type-aware
evidence evaluation, citation integrity and repository isolation.

No network or LLM calls: retrieval runs against a small on-disk fixture
repository (modelled on the Crumbs Angular + Django layout) with mocked
symbol lookups; Qdrant runs in-memory.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.models.investigation_answer import Citation, InvestigationAnswer
from backend.app.services.answer_generation_service import AnswerGenerationService
from backend.app.services.citation_validation_service import (
    CitationValidationResult,
    CitationValidationService,
)
from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
from backend.app.services.investigation_graph import InvestigationGraphService


# ---------------------------------------------------------------------------
# Fixture repository
# ---------------------------------------------------------------------------

FILES = {
    "frontend/src/services/cartservice.ts": """\
import { HttpClient } from '@angular/common/http';
import { environment } from '../environments/environment';

export interface CartItem { productId: number; variant: string; quantity: number; }

export class Cartservice {
  private storageKey = 'cart';
  constructor(private http: HttpClient) {}

  private isLoggedIn(): boolean {
    return !!localStorage.getItem('auth_user');
  }

  loadCart() {
    this.http.get<CartItem[]>(`${environment.apiUrl}/accounts/cart/sync/`, { withCredentials: true })
      .subscribe(items => this.cart.set(items));
  }

  addToCart(productId: any, variant: string, quantity: number) {
    this.saveLocal();
    if (this.isLoggedIn()) {
      this.http.post(`${environment.apiUrl}/accounts/cart/add/`, { productId, variant, quantity }).subscribe();
    }
  }

  private saveLocal() {
    localStorage.setItem(this.storageKey, JSON.stringify(this.cart()));
  }
}
""",
    "frontend/src/app/cart/cart.ts": """\
export class Cart {
  constructor(private cartService: Cartservice) {}

  increaseQty(item: CartItem) {
    item.quantity++;
    this.cartService.addToCart(item.productId, item.variant, 1);
    this.calculateTotal();
  }

  calculateTotal() {
    this.subtotal = this.items.reduce((sum, i) => sum + i.price * i.quantity, 0);
    this.total = this.subtotal + this.shipping;
  }
}
""",
    "frontend/src/app/admin-orders/admin-orders.ts": """\
export class AdminOrders {
  constructor(private orderService: OrderService) {}

  updateStatus(order: any, status: string) {
    this.orderService.updateOrderStatus(order.id, status);
  }
}
""",
    "frontend/src/services/auth.ts": """\
export class Auth {
  constructor(private http: HttpClient) {}

  login(data: { email: string; password: string }) {
    return this.http.post<AuthResponse>(
      `${environment.apiUrl}/accounts/login/`, data, { withCredentials: true }
    ).pipe(tap(res => this.setUser(res.user)));
  }

  checkSession() {
    return this.http.get<SessionResponse>(`${environment.apiUrl}/accounts/session/`, { withCredentials: true });
  }

  private setUser(user: any) {
    localStorage.setItem('auth_user', JSON.stringify(user));
  }
}
""",
    "frontend/src/app/login/login.ts": """\
export class Login {
  constructor(private auth: Auth, private router: Router) {}

  login() {
    this.auth.login({ email: this.email, password: this.password }).subscribe({
      next: () => this.router.navigate(['/']),
      error: () => this.error = 'Invalid credentials',
    });
  }
}
""",
    "frontend/src/main.ts": """\
import { bootstrapApplication } from '@angular/platform-browser';
bootstrapApplication(App, appConfig).catch(err => console.error(err));
""",
    "frontend/src/app/home/home.ts": """\
export class Home {
  slides = [];
}
""",
    "backend/accounts/urls.py": """\
from django.urls import path
from .views import LoginView, SessionView, CartSyncView, CartAddView

urlpatterns = [
    path('login/', LoginView.as_view(), name='login'),
    path('session/', SessionView.as_view(), name='session'),
    path('cart/sync/', CartSyncView.as_view(), name='cart_sync'),
    path('cart/add/', CartAddView.as_view(), name='cart_add'),
]
""",
    "backend/accounts/views.py": """\
from rest_framework.views import APIView
from rest_framework.response import Response
from .models import CartItem
from .serializers import CartItemSerializer


class LoginView(APIView):
    def post(self, request):
        user = authenticate(email=request.data.get('email'), password=request.data.get('password'))
        response = Response({'user': user.email})
        response.set_cookie('access', 'token')
        return response


class SessionView(APIView):
    def get(self, request):
        return Response({'authenticated': request.user.is_authenticated})


class CartSyncView(APIView):
    def get(self, request):
        items = CartItem.objects.filter(user=request.user).select_related('product')
        return Response(CartItemSerializer(items, many=True).data)

    def post(self, request):
        \"\"\"Merge local guest cart into DB - used on fresh login only.\"\"\"
        for entry in request.data.get('items', []):
            cart_item, _ = CartItem.objects.get_or_create(user=request.user, product_id=entry['productId'])
            cart_item.save()
        return Response({'ok': True})


class CartAddView(APIView):
    permission_classes = [IsAuthenticated]
    def post(self, request):
        cart_item, created = CartItem.objects.get_or_create(
            user=request.user, product_id=request.data['productId'], variant=request.data['variant'])
        cart_item.quantity += int(request.data.get('quantity', 1))
        cart_item.save()
        return Response({'message': 'Cart updated'})
""",
    "backend/accounts/serializers.py": """\
from rest_framework import serializers
from .models import CartItem


class CartItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ['id', 'product', 'variant', 'quantity']
""",
    "backend/accounts/models.py": """\
from django.db import models


class CartItem(models.Model):
    user = models.ForeignKey('User', on_delete=models.CASCADE, related_name='cart_items')
    product = models.ForeignKey('catalog.Product', on_delete=models.CASCADE)
    variant = models.CharField(max_length=50)
    quantity = models.PositiveIntegerField(default=1)
""",
    "backend/accounts/admin.py": """\
from django.contrib import admin
from .models import CartItem


@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ('user', 'product', 'quantity')
""",
    "backend/main/settings.py": """\
INSTALLED_APPS = ['django.contrib.admin', 'accounts']

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': 'crumbs',
    }
}

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': ['accounts.authentication.CookieJWTAuthentication'],
}
""",
    "backend/manage.py": """\
import os
from django.core.management import execute_from_command_line

if __name__ == '__main__':
    execute_from_command_line()
""",
}

# Symbol table for the fixture repository: name -> (file, start, end, type)
SYMBOLS = {
    "LoginView": ("backend/accounts/views.py", 7, 12, "class"),
    "SessionView": ("backend/accounts/views.py", 15, 17, "class"),
    "CartSyncView": ("backend/accounts/views.py", 20, 30, "class"),
    "CartAddView": ("backend/accounts/views.py", 33, 40, "class"),
    "CartItemSerializer": ("backend/accounts/serializers.py", 5, 8, "class"),
    "CartItem": ("backend/accounts/models.py", 4, 8, "class"),
    "CartItemAdmin": ("backend/accounts/admin.py", 6, 7, "class"),
    "post": ("backend/accounts/views.py", 35, 40, "method"),
}
# Methods (for enclosing-symbol resolution in reference search)
METHODS = [
    ("post", "LoginView.post", "backend/accounts/views.py", 8, 12),
    ("get", "SessionView.get", "backend/accounts/views.py", 16, 17),
    ("get", "CartSyncView.get", "backend/accounts/views.py", 21, 23),
    ("post", "CartSyncView.post", "backend/accounts/views.py", 25, 30),
    ("post", "CartAddView.post", "backend/accounts/views.py", 35, 40),
    ("LoginView", "LoginView", "backend/accounts/views.py", 7, 12),
    ("CartAddView", "CartAddView", "backend/accounts/views.py", 33, 40),
    ("CartSyncView", "CartSyncView", "backend/accounts/views.py", 20, 30),
]


@pytest.fixture
def repo(tmp_path):
    for rel, content in FILES.items():
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return tmp_path


def _lines(rel: str, start: int, end: int) -> str:
    return "\n".join(FILES[rel].splitlines()[start - 1:end])


def ev(eid: str, rel: str, start: int, end: int, symbol: str = "", **extra) -> dict:
    item = {
        "evidence_id": eid,
        "file": rel,
        "start_line": start,
        "end_line": end,
        "symbol": symbol,
        "symbol_type": "class",
        "snippet": _lines(rel, start, end),
        "relevance_score": 0.9,
        "repository_id": 4,
    }
    item.update(extra)
    return item


def _fake_code_search(db, repository_id, query):
    if repository_id != 4:
        return []
    out = []
    for name, (file, start, end, stype) in SYMBOLS.items():
        if query.lower() in name.lower():
            out.append({"name": name, "file": file, "start_line": start, "end_line": end,
                        "symbol_type": stype, "qualified_name": name})
    return out


def _retrieval_service():
    from backend.app.services.retrieval_service import RetrievalService

    svc = RetrievalService(MagicMock(), MagicMock())
    svc.code_search_service = MagicMock()
    svc.code_search_service.search.side_effect = _fake_code_search
    return svc


def _symbol_db():
    """Mock session whose execute() returns the fixture's method/class symbols."""
    rows = [
        SimpleNamespace(name=n, qualified_name=q, symbol_type="method", start_line=s, end_line=e,
                        file=f)
        for n, q, f, s, e in METHODS
    ]
    db = MagicMock()

    def execute(statement):
        # The statement is filtered by repository and file path; only views.py
        # has symbols in this fixture.
        sql = str(statement.compile(compile_kwargs={"literal_binds": True}))
        result = MagicMock()
        in_views = "views.py" in sql and "repository_id = 4" in sql
        result.scalars.return_value.all.return_value = rows if in_views else []
        return result

    db.execute.side_effect = execute
    return db


def _evaluator():
    llm = MagicMock()
    llm.generate_structured.side_effect = AssertionError("deterministic profiles must not call the LLM")
    return EvidenceEvaluationService(llm)


def _answer(text: str, citations: list[Citation] | None = None, confidence: str = "high") -> InvestigationAnswer:
    return InvestigationAnswer(question="q", answer=text, citations=citations or [], confidence=confidence, limitations=[])


def _cite(item: dict) -> Citation:
    return Citation(
        evidence_id=item["evidence_id"], file=item["file"], start_line=item["start_line"],
        end_line=item["end_line"], symbol=item.get("symbol") or "", snippet=item["snippet"][:200],
        relevance="r",
    )


# Reusable evidence items
UI_INCREASE = lambda eid="E1": ev(eid, "frontend/src/app/cart/cart.ts", 4, 8, "increaseQty")
UI_TOTALS = lambda eid="E9": ev(eid, "frontend/src/app/cart/cart.ts", 10, 13, "calculateTotal")
CLIENT_ADD = lambda eid="E2": ev(eid, "frontend/src/services/cartservice.ts", 19, 24, "addToCart")
CLIENT_LOAD = lambda eid="E2": ev(eid, "frontend/src/services/cartservice.ts", 14, 17, "loadCart")
CLIENT_FULL = lambda eid="E2": ev(eid, "frontend/src/services/cartservice.ts", 6, 29, "Cartservice")
ROUTES = lambda eid="E3": ev(eid, "backend/accounts/urls.py", 4, 9, "urlpatterns")
CART_ADD_VIEW = lambda eid="E4": ev(eid, "backend/accounts/views.py", 33, 40, "CartAddView")
CART_SYNC_VIEW = lambda eid="E5": ev(eid, "backend/accounts/views.py", 20, 30, "CartSyncView")
LOGIN_VIEW = lambda eid="E6": ev(eid, "backend/accounts/views.py", 7, 12, "LoginView")
SESSION_VIEW = lambda eid="E7": ev(eid, "backend/accounts/views.py", 15, 17, "SessionView")
SERIALIZER = lambda eid="E8": ev(eid, "backend/accounts/serializers.py", 5, 8, "CartItemSerializer")
MODEL = lambda eid="E10": ev(eid, "backend/accounts/models.py", 4, 8, "CartItem")
ADMIN = lambda eid="E11": ev(eid, "backend/accounts/admin.py", 5, 7, "CartItemAdmin")
SETTINGS_DB = lambda eid="E12": ev(eid, "backend/main/settings.py", 3, 8, "")
AUTH_SVC = lambda eid="E13": ev(eid, "frontend/src/services/auth.ts", 4, 9, "login")
LOGIN_CMP = lambda eid="E14": ev(eid, "frontend/src/app/login/login.ts", 4, 9, "login")
ADMIN_ORDERS = lambda eid="E15": ev(eid, "frontend/src/app/admin-orders/admin-orders.ts", 4, 6, "updateStatus")


# ===========================================================================
# 1. Exact CartItem symbol lookup
# ===========================================================================

class TestSymbolLookup:
    def test_exact_cartitem_definition_comes_from_models_not_admin(self):
        result = _evaluator().evaluate(
            "Where is the CartItem model defined?", [ADMIN(), MODEL()], "symbol_lookup", 0, 2)
        assert result.sufficiency == "sufficient"
        stage = result.flow_stages[0]
        assert stage.evidence_ids == ["E10"], "only the models.py definition establishes CartItem"

    def test_admin_registration_alone_is_not_a_definition(self):
        result = _evaluator().evaluate(
            "Where is the CartItem model defined?", [ADMIN()], "symbol_lookup", 2, 2)
        assert result.sufficiency == "insufficient"
        assert "symbol_definition" in result.missing_stages


# ===========================================================================
# 2. CartItemSerializer mapping
# ===========================================================================

class TestSerializerMapping:
    def test_serializer_explanation_is_sufficient_and_field_claims_are_grounded(self):
        result = _evaluator().evaluate(
            "How does CartItemSerializer map CartItem fields?", [SERIALIZER()], "file_explanation", 0, 2)
        assert result.sufficiency == "sufficient"
        assert result.missing_stages == []  # no UI/routing stages for a code explanation

        validator = CitationValidationService()
        good = validator.validate_answer_citations(
            _answer("`CartItemSerializer` maps `id`, `product`, `variant` and `quantity` [E8].", [_cite(SERIALIZER())]),
            [SERIALIZER()])
        assert good.is_valid and good.validation_status == "verified"

        bad = validator.validate_answer_citations(
            _answer("`CartItemSerializer` also exposes `product_name` and `line_total` [E8].", [_cite(SERIALIZER())]),
            [SERIALIZER()])
        assert not bad.is_valid
        assert any("product_name" in r or "line_total" in r for r in bad.unsupported_claims)


# ===========================================================================
# 3. Cart retrieval handler discovery
# ===========================================================================

class TestCartRetrievalDiscovery:
    def test_get_cart_call_resolves_to_cart_sync_get_and_its_dependencies(self, repo):
        svc = _retrieval_service()
        extra = svc._relationship_traversal(
            db=MagicMock(), repository_id=4, repository_path=str(repo),
            evidence_items=[CLIENT_LOAD()],
            question="Where is the authenticated user's cart retrieved, and how are cart items returned to the frontend?",
        )
        reasons = [c["traversal"] for c in extra]
        files = {c["file"].replace("\\", "/") for c in extra}
        assert any(r.startswith("route for GET") for r in reasons)
        assert "handler CartSyncView" in reasons
        assert "dependency CartItemSerializer" in reasons
        assert "dependency CartItem" in reasons
        assert "backend/accounts/admin.py" not in files

    def test_cart_retrieval_flow_is_connected(self):
        question = "Where is the authenticated user's cart retrieved, and how are cart items returned to the frontend?"
        result = _evaluator().evaluate(
            question, [CLIENT_LOAD(), ROUTES(), CART_SYNC_VIEW(), SERIALIZER()], "feature_flow", 0, 2)
        stages = {s.stage: s for s in result.flow_stages}
        assert stages["client_service"].connected_to_next
        assert stages["url_routing"].connected_to_next
        assert stages["backend_handler"].present
        assert stages["backend_response"].present
        assert result.unverified_connections == []


# ===========================================================================
# 4. CartAddView route and handler discovery
# ===========================================================================

class TestCartAddDiscovery:
    def test_add_to_cart_resolves_to_cart_add_view_not_cart_sync_view(self, repo):
        svc = _retrieval_service()
        extra = svc._relationship_traversal(
            db=MagicMock(), repository_id=4, repository_path=str(repo),
            evidence_items=[CLIENT_ADD()],
            question="Trace the authenticated cart quantity update from the Angular button click to the database write and response.",
        )
        reasons = [c["traversal"] for c in extra]
        assert "handler CartAddView" in reasons
        assert "handler CartSyncView" not in reasons
        route = next(c for c in extra if c["traversal"].startswith("route for POST"))
        assert "cart/add/" in route["source"]

    def test_route_lookup_is_repository_scoped(self, repo):
        svc = _retrieval_service()
        extra = svc._relationship_traversal(
            db=MagicMock(), repository_id=99, repository_path=str(repo),
            evidence_items=[CLIENT_ADD()], question="cart add",
        )
        # Route lines come from this repository's files; handler definitions
        # are only resolved through repository 99's (empty) symbol table.
        assert not any(c["traversal"].startswith("handler") for c in extra)
        for call in svc.code_search_service.search.call_args_list:
            assert call.kwargs["repository_id"] == 99


# ===========================================================================
# 5. Login POST service, route and backend handler
# ===========================================================================

class TestLoginDiscovery:
    QUESTION = "Trace the user login flow from submitting the login form to successful authentication and frontend session update."

    def test_login_post_resolves_to_login_view(self, repo):
        svc = _retrieval_service()
        extra = svc._relationship_traversal(
            db=MagicMock(), repository_id=4, repository_path=str(repo),
            evidence_items=[ev("E1", "frontend/src/services/auth.ts", 4, 9, "login")],
            question=self.QUESTION,
        )
        reasons = [c["traversal"] for c in extra]
        assert "handler LoginView" in reasons
        assert "handler SessionView" not in reasons

    def test_session_get_is_not_accepted_as_login_post(self):
        result = _evaluator().evaluate(
            self.QUESTION, [LOGIN_CMP(), AUTH_SVC(), ROUTES(), SESSION_VIEW()], "end_to_end_flow", 0, 2)
        assert result.sufficiency != "sufficient"
        assert "backend_handler" in result.missing_stages or result.unverified_connections
        assert "LoginView" in result.recommended_queries["symbol_queries"]

    def test_method_mismatch_is_unverified(self):
        get_only = ev("E6", "backend/accounts/views.py", 15, 17, "LoginView",
                      snippet="class LoginView(APIView):\n    def get(self, request):\n        return Response({})")
        result = _evaluator().evaluate(self.QUESTION, [LOGIN_CMP(), AUTH_SVC(), ROUTES(), get_only], "end_to_end_flow", 2, 2)
        assert result.sufficiency != "sufficient"
        assert any("POST" in c and "LoginView" in c for c in result.unverified_connections)

    def test_complete_login_flow_is_sufficient(self):
        result = _evaluator().evaluate(
            self.QUESTION, [LOGIN_CMP(), AUTH_SVC(), ROUTES(), LOGIN_VIEW()], "end_to_end_flow", 0, 2)
        assert result.sufficiency == "sufficient", result.reasoning
        assert "persistence" not in [s.stage for s in result.flow_stages]


# ===========================================================================
# 6. Architecture retrieval with correct file roles
# ===========================================================================

class TestArchitecture:
    QUESTION = ("Explain the overall architecture of this project, including frontend, backend, database, "
                "API communication and authentication.")

    def test_manage_py_and_a_component_are_not_an_architecture(self):
        result = _evaluator().evaluate(
            self.QUESTION,
            [ev("E1", "backend/manage.py", 1, 5), ev("E2", "frontend/src/app/home/home.ts", 1, 3, "Home")],
            "architectural_overview", 0, 2)
        assert result.sufficiency != "sufficient"
        for stage in ("frontend_entry_point", "configuration", "backend_routing", "persistence_configuration"):
            assert stage in result.missing_stages
        assert "DATABASES" in result.recommended_queries["text_queries"]

    def test_architecture_with_real_roles_is_sufficient(self):
        evidence = [
            ev("E1", "backend/manage.py", 1, 5), ev("E2", "frontend/src/main.ts", 1, 2),
            ev("E3", "backend/main/settings.py", 1, 13), ROUTES("E4"), CLIENT_FULL("E5"),
        ]
        result = _evaluator().evaluate(self.QUESTION, evidence, "architectural_overview", 0, 2)
        assert result.sufficiency == "sufficient", result.missing_stages
        assert any("Database configuration evidence" in n for n in result.grounding_notes)

    def test_staged_retrieval_returns_entry_points_per_area_and_database_window(self, repo):
        from backend.app.models.query_analysis import QuestionType

        svc = _retrieval_service()
        f = lambda p: SimpleNamespace(path=p)
        svc.architectural_file_service = MagicMock()
        svc.architectural_file_service.discover_key_files.return_value = {
            "readme": [], "dependencies": [], "models": [f("backend/accounts/models.py")],
            "entry_points": [f("backend/manage.py"), f("backend/main/settings.py"), f("frontend/src/main.ts")],
            "configuration": [f("backend/main/settings.py")],
            "routes": [f("backend/accounts/views.py"), f("backend/accounts/urls.py")],
        }
        svc.architectural_file_service.get_file_category.side_effect = (
            lambda p: "configuration" if p.endswith("settings.py") else "other")
        out = svc._staged_architectural_retrieval(MagicMock(), 4, str(repo), QuestionType.ARCHITECTURAL_OVERVIEW)
        files = [o["file"] for o in out]
        assert "backend/manage.py" in files and "frontend/src/main.ts" in files
        assert files.index("backend/accounts/urls.py") < files.index("backend/accounts/views.py")
        assert any(o["source"].lstrip().startswith("DATABASES") for o in out)


# ===========================================================================
# 7. Call-site lookup with inline citations
# ===========================================================================

class TestCallSites:
    QUESTION = "What functions and methods call CartItem-related backend operations, and what are their dependencies?"

    def test_reference_search_resolves_enclosing_methods_and_skips_admin_and_imports(self, repo):
        svc = _retrieval_service()
        refs = svc._find_references(_symbol_db(), 4, str(repo), "CartItem", limit=8)
        names = {r.get("qualified_name") for r in refs}
        files = {r["file"].replace("\\", "/") for r in refs}
        assert {"CartSyncView.get", "CartAddView.post"} <= names
        assert "backend/accounts/admin.py" not in files

    def test_call_site_evaluation_and_inline_citations_match_metadata(self):
        result = _evaluator().evaluate(self.QUESTION, [CART_ADD_VIEW(), CART_SYNC_VIEW(), MODEL()], "call_site_lookup", 0, 2)
        assert result.sufficiency == "sufficient"

        llm = MagicMock()
        llm.generate_structured.return_value = {
            "answer": "`CartAddView.post` calls `CartItem.objects.get_or_create` [E4]. "
                      "`CartSyncView.get` uses `CartItem.objects.filter` [E5].",
            "cited_evidence_ids": ["E4"],
            "confidence": "high",
            "limitations": [],
        }
        answer = AnswerGenerationService(llm).generate_answer(
            self.QUESTION, [CART_ADD_VIEW(), CART_SYNC_VIEW(), MODEL()], "call_site_lookup")
        assert [c.evidence_id for c in answer.citations] == ["E4", "E5"]
        validation = CitationValidationService().validate_answer_citations(
            answer, [CART_ADD_VIEW(), CART_SYNC_VIEW(), MODEL()])
        assert validation.is_valid, validation.unsupported_claims


# ===========================================================================
# 8. Guest versus authenticated cart persistence
# ===========================================================================

class TestGuestVersusAuthenticated:
    QUESTION = "How does the application persist cart information for a logged-in customer compared with a guest customer?"

    def test_both_paths_required(self):
        result = _evaluator().evaluate(self.QUESTION, [CLIENT_FULL(), CART_ADD_VIEW()], "feature_flow", 0, 2)
        assert result.evaluation_profile == "database_query"
        assert result.sufficiency == "sufficient", result.missing_stages
        stages = {s.stage: s for s in result.flow_stages}
        assert stages["backend_persistence"].evidence_ids == ["E4"]
        assert stages["guest_storage"].evidence_ids == ["E2"]

    def test_local_storage_alone_does_not_cover_logged_in_persistence(self):
        result = _evaluator().evaluate(self.QUESTION, [CLIENT_FULL()], "feature_flow", 2, 2)
        assert result.sufficiency != "sufficient"
        assert "backend_persistence" in result.missing_stages


# ===========================================================================
# 9. Nonexistent symbol handling
# ===========================================================================

class TestNonexistentSymbol:
    def test_targeted_retry_then_explicit_not_found(self):
        evaluator = _evaluator()
        first = evaluator.evaluate("Where is AutoRefundAllFailedPayments defined?", [CART_ADD_VIEW()], "symbol_lookup", 0, 2)
        assert first.retrieval_needed
        assert "AutoRefundAllFailedPayments" in first.recommended_queries["symbol_queries"]

        last = evaluator.evaluate("Where is AutoRefundAllFailedPayments defined?", [CART_ADD_VIEW()], "symbol_lookup", 2, 2)
        assert not last.retrieval_needed
        assert last.sufficiency == "insufficient"
        assert any("No definition of AutoRefundAllFailedPayments" in m for m in last.missing_claims)

    def test_invented_symbol_in_answer_is_unsupported(self):
        res = CitationValidationService().validate_answer_citations(
            _answer("Refunds are handled by `AutoRefundAllFailedPayments` in the orders module [E4].", [_cite(CART_ADD_VIEW())]),
            [CART_ADD_VIEW()])
        assert not res.is_valid
        assert any("AutoRefundAllFailedPayments" in r for r in res.unsupported_claims)


# ===========================================================================
# 10. ORM explanation versus exact SQL claims
# ===========================================================================

class TestOrmVersusSql:
    def test_sql_claimed_as_observed_is_unsupported(self):
        res = CitationValidationService().validate_answer_citations(
            _answer("CartAddView executes the SQL query `SELECT * FROM accounts_cartitem` [E4].", [_cite(CART_ADD_VIEW())]),
            [CART_ADD_VIEW()])
        assert not res.is_valid
        assert any("SQL" in r for r in res.unsupported_claims)

    def test_labelled_illustrative_sql_and_orm_description_pass(self):
        text = (
            "`CartAddView.post` calls `CartItem.objects.get_or_create` and then `cart_item.save()` [E4].\n\n"
            "Illustrative SQL (inferred, not in the repository):\n"
            "```sql\nUPDATE accounts_cartitem SET quantity = quantity + 1 WHERE id = 1;\n```"
        )
        res = CitationValidationService().validate_answer_citations(_answer(text, [_cite(CART_ADD_VIEW())]), [CART_ADD_VIEW()])
        assert res.is_valid, res.unsupported_claims

    def test_database_engine_requires_configuration_evidence(self):
        validator = CitationValidationService()
        claim = "Cart rows are stored in PostgreSQL through `CartItem.objects` [E4]."
        without = validator.validate_answer_citations(_answer(claim, [_cite(CART_ADD_VIEW())]), [CART_ADD_VIEW()])
        assert any("postgresql" in r for r in without.unsupported_claims)
        withcfg = validator.validate_answer_citations(
            _answer(claim, [_cite(CART_ADD_VIEW())]), [CART_ADD_VIEW(), SETTINGS_DB()])
        assert not any("postgresql" in r for r in withcfg.unsupported_claims)

    def test_evaluator_notes_orm_only_and_unknown_engine(self):
        notes = EvidenceEvaluationService.grounding_notes([CART_ADD_VIEW()])
        assert any("ORM code only" in n for n in notes)
        assert any("do not name the database engine" in n for n in notes)


# ===========================================================================
# 11. Cart quantity / subtotal / total flow keeps canonical evidence IDs
# ===========================================================================

class TestCanonicalEvidenceIds:
    def test_answer_generation_preserves_registry_ids(self):
        registry = [UI_TOTALS("E9"), CART_ADD_VIEW("E4"), ev("E2", "frontend/src/services/cartservice.ts", 1, 1, snippet="")]
        llm = MagicMock()
        llm.generate_structured.return_value = {
            "answer": "`calculateTotal` recomputes `subtotal` and `total` [E9]. "
                      "`CartAddView.post` increments `cart_item.quantity` and calls `cart_item.save()` [E4].",
            "cited_evidence_ids": ["E9", "E4"],
            "confidence": "high",
            "limitations": [],
        }
        answer = AnswerGenerationService(llm).generate_answer("Trace the cart quantity update", registry, "feature_flow")
        by_id = {c.evidence_id: c for c in answer.citations}
        assert by_id["E9"].file == "frontend/src/app/cart/cart.ts" and by_id["E9"].start_line == 10
        assert by_id["E4"].file == "backend/accounts/views.py" and by_id["E4"].start_line == 33
        assert CitationValidationService().validate_answer_citations(answer, registry).is_valid

    def test_full_quantity_flow_verified_and_stage_evidence_ordered_first(self):
        question = "Trace the authenticated cart quantity update from the Angular button click to the database write and response."
        evidence = [ADMIN("E1"), UI_INCREASE("E2"), CLIENT_ADD("E3"), ROUTES("E4"), CART_ADD_VIEW("E5")]
        result = _evaluator().evaluate(question, evidence, "end_to_end_flow", 0, 2)
        assert result.sufficiency == "sufficient", result.unverified_connections
        ordered = InvestigationGraphService._order_evidence_for_answer(evidence, result.model_dump())
        assert ordered[-1]["evidence_id"] == "E1"  # admin registration is not stage evidence


class TestRealDataFindings:
    """Regressions found by running the investigator against the indexed Crumbs repository."""

    QUESTION = "Trace the authenticated cart quantity update from the Angular button click to the database write and response."

    def test_service_methods_are_not_ui_handlers_and_callers_are_requested(self):
        constructor = ev("E8", "frontend/src/services/cartservice.ts", 6, 12, "constructor",
                         snippet="constructor(private http: HttpClient) {\n  this.cart.set(this.load());\n}")
        result = _evaluator().evaluate(self.QUESTION, [constructor, CLIENT_ADD("E6"), ROUTES(), CART_ADD_VIEW()], "end_to_end_flow", 0, 2)
        assert "ui_event_handler" in result.missing_stages
        assert "addToCart" in result.recommended_queries["caller_queries"]

    def test_only_the_question_relevant_call_forms_the_flow(self):
        result = _evaluator().evaluate(self.QUESTION, [UI_INCREASE(), CLIENT_FULL(), ROUTES(), CART_ADD_VIEW(), CART_SYNC_VIEW()],
                                       "end_to_end_flow", 0, 2)
        client = next(s for s in result.flow_stages if s.stage == "client_service")
        assert "cart/add" in client.details and "cart/sync" not in client.details
        assert result.sufficiency == "sufficient", result.unverified_connections

    def test_handler_methods_are_unioned_across_items(self):
        get_item = ev("E5", "backend/accounts/views.py", 20, 23, "CartSyncView")
        post_item = ev("E6", "backend/accounts/views.py", 20, 30, "CartSyncView",
                       snippet="class CartSyncView(APIView):\n    def post(self, request):\n        cart_item.save()\n        return Response({})")
        result = _evaluator().evaluate(
            "Where is the authenticated user's cart retrieved and synced, and how are cart items returned to the frontend?",
            [CLIENT_FULL(), ROUTES(), get_item, post_item], "feature_flow", 0, 2)
        assert not any("implements" in c for c in result.unverified_connections)

    def test_missing_client_is_searched_by_matched_route_path(self):
        question = "Where is the authenticated user's cart retrieved, and how are cart items returned to the frontend?"
        # No client code yet: routes are matched via domain terms only.
        result = _evaluator().evaluate(question, [ROUTES(), CART_SYNC_VIEW()], "feature_flow", 0, 2)
        assert "client_service" in result.missing_stages
        assert "cart/sync/" in result.recommended_queries["text_queries"]

    def test_method_callers_found_by_member_call(self, repo):
        svc = _retrieval_service()
        refs = svc._find_references(_symbol_db(), 4, str(repo), "login", limit=3)
        files = [r["file"].replace("\\", "/") for r in refs]
        assert files == ["frontend/src/app/login/login.ts"]

    def test_redundant_windows_are_not_registered_twice(self):
        registry = [CART_ADD_VIEW("E1"), ev("E2", "backend/accounts/urls.py", 4, 8)]
        assert InvestigationGraphService._is_redundant_window(ev("E3", "backend/accounts/views.py", 35, 40, "post"), registry)
        assert InvestigationGraphService._is_redundant_window(ev("E4", "backend/accounts/urls.py", 5, 9), registry)
        assert not InvestigationGraphService._is_redundant_window(LOGIN_VIEW(), registry)
        # The enclosing class adds the name a route refers to, so it is kept.
        method_only = [ev("E5", "backend/accounts/views.py", 8, 12, "post")]
        assert not InvestigationGraphService._is_redundant_window(LOGIN_VIEW("E6"), method_only)

    def test_session_get_cannot_carry_a_login_submission(self):
        session_call = ev("E3", "frontend/src/services/auth.ts", 11, 13, "checkSession")
        result = _evaluator().evaluate(TestLoginDiscovery.QUESTION, [session_call, ROUTES(), SESSION_VIEW()], "end_to_end_flow", 0, 2)
        assert "client_service" in result.missing_stages
        assert "login/" in result.recommended_queries["text_queries"]

    def test_retrieval_stops_when_only_already_executed_queries_remain(self):
        from backend.app.services.evidence_evaluation_service import EvidenceEvaluation

        svc = InvestigationGraphService.__new__(InvestigationGraphService)
        svc.evidence_evaluation_service = MagicMock()
        svc.evidence_evaluation_service.evaluate.return_value = EvidenceEvaluation(
            sufficiency="partially_sufficient", retrieval_needed=True, reasoning="r",
            recommended_queries={"symbol_queries": [], "text_queries": ["session/"], "import_queries": []},
        )
        state = {"query": "q", "current_round": 0, "max_rounds": 2, "accumulated_evidence": [],
                 "question_type": "end_to_end_flow", "stage_times": {}, "executed_queries": ["text_queries:session/"]}
        assert svc._evaluate_evidence_node(state)["retrieval_needed"] is False
        state["executed_queries"] = []
        assert svc._evaluate_evidence_node(state)["retrieval_needed"] is True

    def test_last_executable_round_is_evaluated_as_final(self):
        svc = InvestigationGraphService.__new__(InvestigationGraphService)
        svc.evidence_evaluation_service = _evaluator()
        state = {"query": TestLoginDiscovery.QUESTION, "current_round": 1, "max_rounds": 2,
                 "accumulated_evidence": [AUTH_SVC(), ROUTES(), LOGIN_VIEW()],
                 "question_type": "end_to_end_flow", "stage_times": {}}
        out = svc._evaluate_evidence_node(state)
        # Routing only runs round index 2 if 2 < max_rounds, so round 1 is final.
        assert out["retrieval_needed"] is False
        assert out["current_round"] == 1
        assert "Maximum retrieval rounds reached" in out["evidence_evaluation"]["reasoning"]

    def test_enclosing_method_survives_blank_lines(self):
        from backend.app.services import evidence_signals as sig

        code = (
            "  addToCart(productId: any, quantity: number) {\n"
            "    const item = this.find(productId);\n"
            "\n"
            "    if (this.isLoggedIn()) {\n"
            "      this.http.post(`${environment.apiUrl}/accounts/cart/add/`, { productId }).subscribe();\n"
            "    }\n"
            "  }\n"
        )
        assert [c["enclosing"] for c in sig.extract_http_calls(code)] == ["addToCart"]

    def test_traversal_finds_ui_caller_of_client_method_in_same_round(self, repo):
        svc = _retrieval_service()
        extra = svc._relationship_traversal(
            db=_symbol_db(), repository_id=4, repository_path=str(repo),
            evidence_items=[CLIENT_ADD()], question=TestAdminExcluded.QUESTION,
        )
        callers = [c for c in extra if c["traversal"] == "caller of addToCart"]
        assert callers and callers[0]["file"].replace("\\", "/") == "frontend/src/app/cart/cart.ts"

    def test_absence_statement_about_requested_symbol_is_not_fabrication(self):
        res = CitationValidationService().validate_answer_citations(
            _answer("None of the retrieved files contain a definition of `AutoRefundAllFailedPayments`."), [CART_ADD_VIEW()])
        assert res.unsupported_claims == []
        assert res.validation_status == "not_evaluated"

    def test_bare_and_mispaired_markers(self):
        from backend.app.services.citation_validation_service import normalize_citation_markers

        assert normalize_citation_markers("Evidence: E8 (and duplicated in E11). `E1 = 2`") == \
            "Evidence: [E8] (and duplicated in [E11]). `E1 = 2`"
        assert normalize_citation_markers("component 【[E14]】 and 【E4】.") == "component [E14] and [E4]."
        res = CitationValidationService().validate_answer_citations(
            _answer("It is defined as `def post(self, request):`), which extracts `variant` [E4].", [_cite(CART_ADD_VIEW())]),
            [CART_ADD_VIEW()])
        assert res.is_valid, res.unsupported_claims

    def test_parenthesised_citation_markers_are_normalised(self):
        from backend.app.services.citation_validation_service import normalize_citation_markers

        text = "Saves via `cart_item.save()` (E4). Model (see E10). Both [E4, E10]. Also (E4 and E10)."
        assert normalize_citation_markers(text) == (
            "Saves via `cart_item.save()` [E4]. Model [E10]. Both [E4][E10]. Also [E4][E10]."
        )
        llm = MagicMock()
        llm.generate_structured.return_value = {
            "answer": "`CartAddView.post` calls `cart_item.save()` (E4).",
            "cited_evidence_ids": [], "confidence": "high", "limitations": [],
        }
        answer = AnswerGenerationService(llm).generate_answer("q", [CART_ADD_VIEW()], "feature_flow")
        assert [c.evidence_id for c in answer.citations] == ["E4"]

    def test_call_expression_grounded_by_callee(self):
        res = CitationValidationService().validate_answer_citations(
            _answer("The component calls `this.cartService.addToCart(item.productId, item.variant, delta)` [E1].",
                    [_cite(UI_INCREASE())]), [UI_INCREASE()])
        assert res.is_valid, res.unsupported_claims

    def test_single_unsupported_claim_is_removed_without_llm_rewrite(self):
        llm = MagicMock()
        registry = [UI_INCREASE(), CART_ADD_VIEW()]
        answer = _answer(
            "`Cart.increaseQty` calls `this.cartService.addToCart` [E1]. "
            "`CartAddView.post` calls `CartItem.objects.get_or_create` [E4]. "
            "It then saves with `cart_item.save()` [E4]. "
            "The cart is mirrored to `sessionStorage` [E1].",
            [_cite(UI_INCREASE()), _cite(CART_ADD_VIEW())])
        svc = CitationValidationService(llm)
        repaired = svc.repair_answer(answer, registry, svc.validate_answer_citations(answer, registry))
        llm.generate_structured.assert_not_called()
        assert "sessionStorage" not in repaired.answer.split("**Unverified statements removed:**")[0]
        assert [c.evidence_id for c in repaired.citations] == ["E1", "E4"]

    def test_answer_without_inline_citations_is_not_verified_and_gets_rewritten(self):
        registry = [CART_ADD_VIEW()]
        uncited = _answer("`CartAddView.post` calls `cart_item.save()` to persist the quantity.", [_cite(CART_ADD_VIEW())])
        svc = CitationValidationService(MagicMock())
        val = svc.validate_answer_citations(uncited, registry)
        assert not val.is_valid and val.repair_needed
        assert any("cites no evidence inline" in r for r in val.unsupported_claims)

        svc.llm_service.generate_structured.return_value = {
            "answer": "`CartAddView.post` calls `cart_item.save()` to persist the quantity [E4].",
            "cited_evidence_ids": ["E4"], "confidence": "high", "limitations": [],
        }
        repaired = svc.repair_answer(uncited, registry, val)
        svc.llm_service.generate_structured.assert_called_once()
        assert svc.validate_answer_citations(repaired, registry).is_valid

    def test_malformed_llm_output_is_retried_once(self):
        llm = MagicMock()
        llm.generate_structured.side_effect = [
            RuntimeError("Failed to parse LLM response as JSON: Expecting property name"),
            {"answer": "`CartAddView.post` calls `cart_item.save()` [E4].", "cited_evidence_ids": ["E4"],
             "confidence": "high", "limitations": []},
        ]
        answer = AnswerGenerationService(llm).generate_answer("q", [CART_ADD_VIEW()], "feature_flow")
        assert llm.generate_structured.call_count == 2
        assert [c.evidence_id for c in answer.citations] == ["E4"]

        llm = MagicMock()
        llm.generate_structured.side_effect = RuntimeError("GROQ_API_KEY not configured")
        with pytest.raises(RuntimeError):
            AnswerGenerationService(llm).generate_answer("q", [CART_ADD_VIEW()], "feature_flow")
        assert llm.generate_structured.call_count == 1

    def test_relationship_evidence_survives_the_per_round_cut(self):
        from backend.app.services.evidence_collector_service import EvidenceCollectorService
        from backend.app.models.query_analysis import QuestionType

        generic = [
            {"file": f"backend/accounts/urls.py", "start_line": i, "end_line": i + 4, "source": "path('login/', LoginView.as_view())"}
            for i in range(1, 15)
        ]
        caller = {"file": "frontend/src/app/login/login.ts", "start_line": 4, "end_line": 9,
                  "source": _lines("frontend/src/app/login/login.ts", 4, 9), "traversal": "caller of login"}
        out = EvidenceCollectorService().collect(
            {"results": {"source_context": generic + [caller]}, "query": TestLoginDiscovery.QUESTION, "query_analysis": {}},
            max_evidence=12, question_type=QuestionType.END_TO_END_FLOW)
        assert out["evidence"][0]["file"] == "frontend/src/app/login/login.ts"

    def test_failed_generation_is_not_sent_to_repair(self):
        svc = InvestigationGraphService.__new__(InvestigationGraphService)
        state = {
            "answer": _answer("Unable to generate answer due to an internal error: `CartAddView` ...", confidence="insufficient"),
            "citation_validation": {"repair_needed": True}, "repair_attempts": 0, "max_repair_attempts": 1,
        }
        assert svc._route_after_citation_validation(state) == "finalize"

    def test_citations_mirror_inline_references(self):
        llm = MagicMock()
        llm.generate_structured.return_value = {
            "answer": "`CartAddView.post` calls `cart_item.save()` [E4].",
            "cited_evidence_ids": ["E4", "E3", "E10"], "confidence": "high", "limitations": [],
        }
        answer = AnswerGenerationService(llm).generate_answer("q", [ROUTES(), CART_ADD_VIEW(), MODEL()], "feature_flow")
        assert [c.evidence_id for c in answer.citations] == ["E4"]


# ===========================================================================
# 12. Repository ID isolation
# ===========================================================================

class TestRepositoryIsolation:
    def test_semantic_search_drops_foreign_points_and_scopes_file_lookup(self):
        from backend.app.services.semantic_search_service import SemanticSearchService

        vectors = MagicMock()
        vectors.search.return_value = [
            SimpleNamespace(id=1, score=0.9, payload={"repository_id": 4, "file_id": 10, "symbol_id": None,
                                                      "start_line": 1, "end_line": 5, "content": "mine"}),
            SimpleNamespace(id=2, score=0.95, payload={"repository_id": 7, "file_id": 70, "symbol_id": None,
                                                       "start_line": 1, "end_line": 5, "content": "foreign"}),
        ]
        embed = MagicMock()
        embed.embed.return_value = [0.0] * 384
        db = MagicMock()
        db.scalars.return_value.all.side_effect = [
            [SimpleNamespace(id=10, path="backend/accounts/views.py", language="python")], []]
        results = SemanticSearchService(embed, vectors).search(db, 4, "cart")

        assert [r["content"] for r in results] == ["mine"]
        assert vectors.search.call_args.kwargs["repository_id"] == 4
        file_query = str(db.scalars.call_args_list[0].args[0])
        assert "repository_id" in file_query

    def test_qdrant_search_and_delete_are_repository_scoped(self):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, PointStruct, VectorParams

        from backend.app.services.vector_service import VectorService

        svc = VectorService.__new__(VectorService)
        svc.client = QdrantClient(":memory:")
        svc.client.create_collection(svc.COLLECTION_NAME, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
        svc.upsert([
            PointStruct(id=1, vector=[1.0, 0.0, 0.0, 0.0], payload={"repository_id": 1}),
            PointStruct(id=2, vector=[1.0, 0.0, 0.0, 0.0], payload={"repository_id": 2}),
            PointStruct(id=3, vector=[0.0, 1.0, 0.0, 0.0], payload={"repository_id": 2}),
        ])
        assert {p.id for p in svc.search([1.0, 0.0, 0.0, 0.0], repository_id=1)} == {1}

        # 19. Deleting repository 1 removes only its vectors.
        svc.delete_repository(1)
        assert svc.search([1.0, 0.0, 0.0, 0.0], repository_id=1) == []
        assert {p.id for p in svc.search([1.0, 0.0, 0.0, 0.0], repository_id=2)} == {2, 3}

    def test_validator_rejects_evidence_from_another_repository(self):
        foreign = CART_ADD_VIEW()
        foreign["repository_id"] = 7
        res = CitationValidationService().validate_answer_citations(
            _answer("`CartAddView.post` calls `cart_item.save()` [E4].", [_cite(foreign)]), [foreign], repository_id=4)
        assert not res.is_valid
        assert any("repository" in c["reason"] for c in res.invalid_citations)


# ===========================================================================
# 13. Admin file excluded from runtime persistence evidence
# ===========================================================================

class TestAdminExcluded:
    QUESTION = "Trace the cart quantity update from the Angular button click to the database write."

    def test_admin_registration_is_not_persistence(self):
        result = _evaluator().evaluate(self.QUESTION, [UI_INCREASE(), CLIENT_ADD(), ROUTES(), ADMIN()], "end_to_end_flow", 2, 2)
        assert "persistence" in result.missing_stages
        assert result.sufficiency != "sufficient"

    def test_admin_ranked_below_implementation(self):
        from backend.app.services.evidence_collector_service import EvidenceCollectorService
        from backend.app.models.query_analysis import QuestionType

        results = {"results": {"source_context": [
            {"file": "backend/accounts/admin.py", "start_line": 5, "end_line": 7, "source": FILES["backend/accounts/admin.py"]},
            {"file": "backend/accounts/views.py", "start_line": 33, "end_line": 40, "source": _lines("backend/accounts/views.py", 33, 40)},
        ]}, "query": "How is CartItem saved?", "query_analysis": {"symbol_queries": ["CartItem"]}}
        ranked = EvidenceCollectorService().collect(results, question_type=QuestionType.FEATURE_FLOW)["evidence"]
        assert ranked[0]["file"] == "backend/accounts/views.py"

    def test_claim_citing_admin_for_database_write_is_unsupported(self):
        res = CitationValidationService().validate_answer_citations(
            _answer("The quantity is saved to the database through `CartItemAdmin` [E11].", [_cite(ADMIN())]), [ADMIN()])
        assert any("admin registration" in r for r in res.unsupported_claims)


# ===========================================================================
# 14. localStorage excluded from backend database persistence
# ===========================================================================

class TestLocalStorageExcluded:
    def test_local_storage_is_not_a_database_write(self):
        question = "Trace the cart quantity update from the Angular button click to the database write."
        result = _evaluator().evaluate(question, [UI_INCREASE(), CLIENT_FULL()], "end_to_end_flow", 2, 2)
        assert "persistence" in result.missing_stages
        assert any("localStorage" in n or "browser storage" in n for n in result.grounding_notes)

    def test_claim_citing_local_storage_for_database_is_unsupported(self):
        item = ev("E2", "frontend/src/services/cartservice.ts", 26, 28, "saveLocal")
        res = CitationValidationService().validate_answer_citations(
            _answer("`saveLocal` saves the cart to the database [E2].", [_cite(item)]), [item])
        assert any("client-side storage" in r for r in res.unsupported_claims)


# ===========================================================================
# 15. Zero evaluated claims cannot pass validation
# ===========================================================================

class TestZeroClaims:
    def test_prose_only_answer_is_not_evaluated(self):
        res = CitationValidationService().validate_answer_citations(
            _answer("The application has a cart. Users can buy things."), [CART_ADD_VIEW()])
        assert res.validation_status == "not_evaluated"
        assert res.is_valid is False
        assert res.to_dict()["evaluated_claims_count"] == 0
        assert "all supported" not in res.details

    def test_finalize_downgrades_high_confidence_when_not_verified(self):
        svc = InvestigationGraphService.__new__(InvestigationGraphService)
        state = {
            "answer": _answer("The application has a cart.", confidence="high"),
            "query": "q", "repository_info": {}, "start_time": 0.0,
            "citation_validation": {"validation_status": "not_evaluated", "details": "Not evaluated"},
        }
        response = svc._finalize_response_node(state)["response"]
        assert response.investigation.confidence == "partial"

    def test_claims_inside_code_blocks_and_lists_are_evaluated(self):
        text = (
            "Steps:\n"
            "1. `Cart.increaseQty` calls `this.cartService.addToCart` [E1]\n"
            "2. `CartAddView.post` saves the item [E4]\n\n"
            "Code [E4]:\n```python\ncart_item.quantity += int(request.data.get('quantity', 1))\ncart_item.save()\n```"
        )
        res = CitationValidationService().validate_answer_citations(
            _answer(text, [_cite(UI_INCREASE()), _cite(CART_ADD_VIEW())]), [UI_INCREASE(), CART_ADD_VIEW()])
        assert len(res.evaluated_claims) >= 3
        assert res.is_valid, res.unsupported_claims

    def test_fabricated_code_block_is_unsupported(self):
        text = "Code [E4]:\n```python\nCartItem.objects.filter(user=request.user).update(quantity=F('quantity') + 1)\nlog_cart_event(request.user)\n```"
        res = CitationValidationService().validate_answer_citations(_answer(text, [_cite(CART_ADD_VIEW())]), [CART_ADD_VIEW()])
        assert not res.is_valid
        assert any("Code block" in r for r in res.unsupported_claims)


# ===========================================================================
# 16. Invalid citation locations cannot pass validation
# ===========================================================================

class TestCitationLocations:
    def _validate(self, repo, citation, item):
        return CitationValidationService().validate_answer_citations(
            _answer("`CartAddView.post` calls `cart_item.save()` [E4].", [citation]), [item], repository_path=str(repo))

    def test_valid_location_passes(self, repo):
        res = self._validate(repo, _cite(CART_ADD_VIEW()), CART_ADD_VIEW())
        assert res.is_valid, res.invalid_citations

    def test_lines_beyond_file_length_fail(self, repo):
        item = CART_ADD_VIEW()
        item.update(start_line=300, end_line=320)
        res = self._validate(repo, _cite(item), item)
        assert not res.is_valid
        assert any("exceed file length" in c["reason"] for c in res.invalid_citations)

    def test_snippet_not_at_cited_lines_fails(self, repo):
        item = CART_ADD_VIEW()
        item.update(start_line=7, end_line=12)  # LoginView lines, CartAddView snippet
        res = self._validate(repo, _cite(item), item)
        assert any("does not match" in c["reason"] for c in res.invalid_citations)

    def test_citation_lines_must_match_registry(self, repo):
        citation = _cite(CART_ADD_VIEW())
        citation.start_line, citation.end_line = 34, 35
        res = self._validate(repo, citation, CART_ADD_VIEW())
        assert any("Line mismatch" in c["reason"] for c in res.invalid_citations)


# ===========================================================================
# 17. Answer citation IDs and returned citation metadata must match
# ===========================================================================

class TestCitationConsistency:
    def test_inline_id_missing_from_citation_list_fails(self):
        answer = _answer("`CartAddView.post` calls `cart_item.save()` [E4]. `CartSyncView.get` uses `CartItem.objects.filter` [E5].",
                         [_cite(CART_ADD_VIEW())])
        res = CitationValidationService().validate_answer_citations(answer, [CART_ADD_VIEW(), CART_SYNC_VIEW()])
        assert res.missing_citation_ids == ["E5"]
        assert res.repair_needed and not res.is_valid


# ===========================================================================
# 18. Failed citation repair produces a safe partial answer
# ===========================================================================

class TestRepair:
    def test_deterministic_repair_removes_unsupported_claims_and_rebuilds_citations(self):
        registry = [CART_ADD_VIEW(), CART_SYNC_VIEW()]
        answer = _answer(
            "`CartAddView.post` calls `cart_item.save()` [E4]. "
            "It then emails the customer via `send_cart_email` [E4]. "
            "Prices come from `PricingEngine` [E9].",
            [_cite(CART_ADD_VIEW())])
        svc = CitationValidationService()
        val = svc.validate_answer_citations(answer, registry)
        repaired = svc.repair_answer(answer, registry, val)

        assert "send_cart_email" not in repaired.answer.split("**Unverified statements removed:**")[0]
        assert "[E9]" not in repaired.answer
        assert [c.evidence_id for c in repaired.citations] == ["E4"]
        assert repaired.confidence == "partial"
        assert svc.validate_answer_citations(repaired, registry).is_valid

    def test_llm_repair_cannot_introduce_new_evidence_ids(self):
        llm = MagicMock()
        llm.generate_structured.return_value = {
            "answer": "`CartAddView.post` calls `cart_item.save()` [E4]. A cache layer stores totals [E42].",
            "cited_evidence_ids": ["E4", "E42"], "confidence": "high", "limitations": [],
        }
        registry = [CART_ADD_VIEW()]
        bad = _answer("Totals come from `PricingEngine` [E4].", [_cite(CART_ADD_VIEW())])
        svc = CitationValidationService(llm)
        repaired = svc.repair_answer(bad, registry, svc.validate_answer_citations(bad, registry))
        assert "[E42]" not in repaired.answer
        assert {c.evidence_id for c in repaired.citations} == {"E4"}
        assert repaired.confidence == "partial"

    def test_everything_unsupported_yields_insufficient_safe_answer(self):
        registry = [CART_ADD_VIEW()]
        answer = _answer("Totals come from `PricingEngine` [E4].", [_cite(CART_ADD_VIEW())])
        svc = CitationValidationService()
        repaired = svc.repair_answer(answer, registry, svc.validate_answer_citations(answer, registry))
        assert repaired.confidence == "insufficient"
        assert repaired.citations == []
        assert "Unverified statements removed" in repaired.answer


# ===========================================================================
# 19. Repository deletion cleans up only that repository's vectors
# ===========================================================================

class TestRepositoryDeletion:
    def _service(self):
        from backend.app.services.repository_service import RepositoryService

        svc = RepositoryService.__new__(RepositoryService)
        svc.delete_repository = MagicMock(return_value=True)
        return svc

    def test_vector_failure_leaves_postgres_intact_for_retry(self):
        svc = self._service()
        db = MagicMock()
        db.get.return_value = object()
        vectors = MagicMock()
        vectors.delete_repository.side_effect = RuntimeError("qdrant down")

        assert svc.delete_repository_everywhere(db, 4, vectors) == "vector_delete_failed"
        svc.delete_repository.assert_not_called()

        vectors.delete_repository.side_effect = None
        assert svc.delete_repository_everywhere(db, 4, vectors) == "deleted"
        vectors.delete_repository.assert_called_with(4)
        svc.delete_repository.assert_called_once_with(db, 4)

    def test_unknown_repository_touches_nothing(self):
        svc = self._service()
        db = MagicMock()
        db.get.return_value = None
        vectors = MagicMock()
        assert svc.delete_repository_everywhere(db, 5, vectors) == "not_found"
        vectors.delete_repository.assert_not_called()

    def test_postgres_delete_rolls_back_on_failure(self):
        from backend.app.services.repository_service import RepositoryService

        svc = RepositoryService.__new__(RepositoryService)
        db = MagicMock()
        db.get.return_value = object()
        db.scalars.return_value.all.return_value = [1, 2]
        db.commit.side_effect = RuntimeError("db error")
        with pytest.raises(RuntimeError):
            svc.delete_repository(db, 4)
        db.rollback.assert_called_once()
