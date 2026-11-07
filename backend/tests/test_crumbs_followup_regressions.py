"""
Deterministic regressions for the Crumbs follow-up issues: CartItem call-site
coverage, payment-capture tracing, cart subtotal/total calculation, complete
authenticated cart flows, and evaluator/validator false positives.

Evidence snippets mirror the real Crumbs code. No network or LLM calls.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from backend.app.models.investigation_answer import Citation, InvestigationAnswer
from backend.app.services import evidence_signals as sig
from backend.app.services.citation_validation_service import CitationValidationService
from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService
from backend.app.services.investigation_graph import InvestigationGraphService


def item(eid, file, start, snippet, symbol="", qualified=None, symbol_type="method"):
    return {
        "evidence_id": eid, "file": file, "start_line": start,
        "end_line": start + snippet.count("\n"), "symbol": symbol,
        "qualified_name": qualified or symbol, "symbol_type": symbol_type,
        "snippet": snippet, "relevance_score": 0.9, "repository_id": 4,
    }


def evaluator():
    llm = MagicMock()
    llm.generate_structured.side_effect = AssertionError("deterministic profiles must not call the LLM")
    return EvidenceEvaluationService(llm)


def answer(text, evidence):
    cites = [
        Citation(evidence_id=e["evidence_id"], file=e["file"], start_line=e["start_line"], end_line=e["end_line"],
                 symbol=e.get("symbol") or "", snippet=e["snippet"][:200], relevance="r")
        for e in evidence if f"[{e['evidence_id']}]" in text
    ]
    return InvestigationAnswer(question="q", answer=text, citations=cites, confidence="high", limitations=[])


# ---------------------------------------------------------------------------
# Evidence modelled on Crumbs
# ---------------------------------------------------------------------------

CART_TS_INCREASE = """\
  increaseQty(item: any) {
    item.quantity++;
    this.cartService.addToCart(item.productId, item.variant, 1);
    item.subtotal = item.quantity * item.price;
    this.calculateTotal();
  }"""
CART_TS_DECREASE = """\
  decreaseQty(item: any) {
    if (item.quantity > 1) {
      item.quantity--;
      this.cartService.addToCart(item.productId, item.variant, -1);
      item.subtotal = item.quantity * item.price;
      this.calculateTotal();
    }
  }"""
CART_TS_LOAD = """\
  loadCart() {
    const cart: CartItem[] = this.cartService.cart();
    this.cartItems = cart.map(item => {
      const variant = product.variants.find(v => v.weight === item.variant);
      const discountedPrice = parseFloat(variant.discounted_price);
      const originalPrice = parseFloat(variant.original_price);
      return {
        actualsubtotal: originalPrice * item.quantity,
        subtotal: discountedPrice * item.quantity
      };
    });
    this.calculateTotal();
  }

  calculateTotal() {
    this.totalAmount = this.cartItems.reduce((sum, item) => sum + item.subtotal, 0);
  }"""
SVC_ADD = """\
  addToCart(productId: number | string, variant: string, quantity: number) {
    this.cart.update(cart => {
      const existing = cart.find(item => item.productId === productId && item.variant === variant);
      if (existing) {
        existing.quantity += quantity;
      }
      this.saveToLocal(cart);
      return [...cart];
    });

    if (this.isLoggedIn()) {
      this.http.post(`${environment.apiUrl}/accounts/cart/add/`, { productId, variant, quantity }, { withCredentials: true }).subscribe();
    }
  }"""
SVC_LOAD = """\
  loadFromDB() {
    this.http.get<CartItem[]>(`${environment.apiUrl}/accounts/cart/sync/`, { withCredentials: true })
      .subscribe(dbCart => {
        this.cart.set(dbCart);
        this.saveToLocal(dbCart);
      });
  }"""
SVC_LOAD_DISCARDED = """\
  loadFromDB() {
    this.http.get<CartItem[]>(`${environment.apiUrl}/accounts/cart/sync/`, { withCredentials: true }).subscribe();
  }"""
AUTH_RESTORE = """\
  private setCurrentUser(user: AuthUser, isNewLogin: boolean = false) {
    if (isNewLogin) {
      this.cartService.syncCart();
    } else {
      this.cartService.loadFromDB();
    }
  }"""
URLS = """\
    path('cart/sync/', CartSyncView.as_view(), name='cart_sync'),
    path('cart/add/', CartAddView.as_view(), name='cart_add'),
    path('cart/remove/', CartRemoveView.as_view(), name='cart_remove'),
    path('cart/clear/', CartClearView.as_view(), name='cart_clear'),"""
SYNC_VIEW = """\
class CartSyncView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        cart_items = CartItem.objects.filter(user=request.user)
        serializer = CartItemSerializer(cart_items, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        for item in request.data.get('items', []):
            cart_item, created = CartItem.objects.get_or_create(user=request.user, product=item['productId'])
            cart_item.save()
        return Response({'ok': True})"""
SYNC_VIEW_NO_AUTH = SYNC_VIEW.replace("    permission_classes = [IsAuthenticated]\n", "    \n")
ADD_VIEW = """\
class CartAddView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        quantity = request.data.get('quantity', 1)
        cart_item, created = CartItem.objects.get_or_create(user=request.user, product=product, variant=variant,
                                                            defaults={'quantity': quantity})
        if not created:
            cart_item.quantity += quantity
            if cart_item.quantity <= 0:
                cart_item.delete()
            else:
                cart_item.save()
        return Response({'message': 'Cart updated'}, status=status.HTTP_200_OK)"""
REMOVE_VIEW = """\
class CartRemoveView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        CartItem.objects.filter(user=request.user, product_id=product_id, variant=variant).delete()
        return Response({'message': 'Item removed'}, status=status.HTTP_200_OK)"""
CLEAR_VIEW = """\
class CartClearView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        CartItem.objects.filter(user=request.user).delete()
        return Response({'message': 'Cart cleared'}, status=status.HTTP_200_OK)"""
SERIALIZER = """\
class CartItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ['id', 'productId', 'variant', 'quantity']"""
WEBHOOK_POST = """\
    def post(self, request):
        payload = json.loads(request.body)
        event = payload.get('event', '')
        if event == 'payment.captured':
            self._handle_payment_captured(payload)
        return Response({'status': 'ok'}, status=status.HTTP_200_OK)"""
HANDLE_CAPTURED = """\
    def _handle_payment_captured(self, payload):
        try:
            payment_entity = payload['payload']['payment']['entity']
            razorpay_order_id = payment_entity.get('order_id', '')
            order = Order.objects.filter(razorpay_order_id=razorpay_order_id).first()
            if not order:
                return
            order.status = 'Paid'
            order.save(update_fields=['status', 'payment_id', 'updated_at'])
            OrderStatusHistory.objects.create(order=order, new_status='Paid')
            # Clear cart as a safety net (normally cleared during verify step)
            CartItem.objects.filter(user=order.user).delete()
        except (KeyError, Exception) as exc:
            logger.error('Webhook payment.captured handler error: %s', exc)"""
CHECKOUT_TOTAL = """\
    def post(self, request):
        cart_items = CartItem.objects.filter(user=user)
        total_amount = Decimal('0.00')
        for item in cart_items:
            price = variant.discounted_price
            total_amount += (price * item.quantity)
        return Response(OrderSerializer(order).data)"""
VARIANT_MODEL = """\
class ProductVariant(models.Model):
    weight = models.CharField(max_length=50)
    original_price = models.DecimalField(max_digits=10, decimal_places=2)
    discounted_price = models.DecimalField(max_digits=10, decimal_places=2)"""

F_CART = "frontend/src/app/cart/cart.ts"
F_SVC = "frontend/src/services/cartservice.ts"
F_AUTH = "frontend/src/services/auth.ts"
F_URLS = "backend/accounts/urls.py"
F_VIEWS = "backend/accounts/views.py"
F_ORDERS = "backend/orders/views.py"

UI_INC = lambda: item("E1", F_CART, 80, CART_TS_INCREASE, "increaseQty", "Cart.increaseQty")
UI_DEC = lambda: item("E2", F_CART, 87, CART_TS_DECREASE, "decreaseQty", "Cart.decreaseQty")
CLIENT_ADD = lambda: item("E3", F_SVC, 53, SVC_ADD, "addToCart", "Cartservice.addToCart")
ROUTES = lambda: item("E4", F_URLS, 36, URLS, "", None, "")
ADD = lambda: item("E5", F_VIEWS, 334, ADD_VIEW, "CartAddView", None, "class")
CLIENT_LOAD = lambda: item("E6", F_SVC, 45, SVC_LOAD, "loadFromDB", "Cartservice.loadFromDB")
RESTORE = lambda: item("E7", F_AUTH, 222, AUTH_RESTORE, "setCurrentUser", "Auth.setCurrentUser")
SYNC = lambda: item("E8", F_VIEWS, 292, SYNC_VIEW, "CartSyncView", None, "class")
SER = lambda: item("E9", "backend/accounts/serializers.py", 133, SERIALIZER, "CartItemSerializer", None, "class")
REMOVE = lambda: item("E10", F_VIEWS, 362, REMOVE_VIEW, "CartRemoveView", None, "class")
CLEAR = lambda: item("E11", F_VIEWS, 372, CLEAR_VIEW, "CartClearView", None, "class")
WEBHOOK = lambda: item("E12", F_ORDERS, 640, WEBHOOK_POST, "post", "RazorpayWebhookView.post")
CAPTURED = lambda: item("E13", F_ORDERS, 678, HANDLE_CAPTURED, "_handle_payment_captured",
                        "RazorpayWebhookView._handle_payment_captured")
CART_LOAD = lambda: item("E14", F_CART, 42, CART_TS_LOAD, "loadCart", "Cart.loadCart")
CHECKOUT = lambda: item("E15", F_ORDERS, 35, CHECKOUT_TOTAL, "post", "CheckoutView.post")
VARIANT = lambda: item("E16", "backend/catalog/models.py", 72, VARIANT_MODEL, "ProductVariant", None, "class")

UPDATE_Q = "How does an authenticated user update the quantity of a cart item?"
RETRIEVE_Q = "How does an authenticated user retrieve their cart from the backend?"
CALL_SITE_Q = "Find all call sites and CRUD operations on the CartItem model"
PAYMENT_Q = "What happens when a payment is captured? Trace _handle_payment_captured"
TOTAL_Q = "How are the cart subtotal, quantity and total calculated?"


def stage(result, name):
    return next(s for s in result.flow_stages if s.stage == name)


# ===========================================================================
# Signals
# ===========================================================================

class TestSignals:
    def test_response_consumption_distinguishes_consumed_ignored_returned(self):
        add = sig.extract_http_calls(SVC_ADD)[0]
        load = sig.extract_http_calls(SVC_LOAD)[0]
        assert sig.response_consumption(SVC_ADD, add["position"]) == "ignored"
        assert sig.response_consumption(SVC_LOAD, load["position"]) == "consumed"
        returned = "  login(d) {\n    return this.http.post(`${api}/accounts/login/`, d);\n  }"
        call = sig.extract_http_calls(returned)[0]
        assert sig.response_consumption(returned, call["position"]) == "returned"
        unused = "  ping() {\n    this.http.get(`${api}/ping/`).subscribe(res => console.log('done'));\n  }"
        assert sig.response_consumption(unused, sig.extract_http_calls(unused)[0]["position"]) == "ignored"

    def test_orm_operations_classify_crud(self):
        kinds = [(op["model"], op["kind"]) for op in sig.orm_operations(HANDLE_CAPTURED, 678)]
        assert kinds == [("Order", "read"), (None, "update"), ("OrderStatusHistory", "create"), ("CartItem", "delete")]
        assert sig.orm_operations(HANDLE_CAPTURED, 678)[-1]["line"] == 689

    def test_method_body_and_helper_calls_ignore_comments_and_keywords(self):
        body = sig.method_body("class W:\n" + WEBHOOK_POST + "\n\n" + HANDLE_CAPTURED, "_handle_payment_captured")
        assert body.startswith("    def _handle_payment_captured") and "logger.error" in body
        assert sig.helper_calls(body) == []  # 'net' (comment) / 'except' are not calls
        assert sig.helper_calls(sig.method_body(WEBHOOK_POST, "post")) == ["_handle_payment_captured"]
        assert "calculateTotal()" in sig.method_body(CART_TS_LOAD, "calculateTotal")

    def test_computations_and_increment_decrement(self):
        assert [c["code"] for c in sig.computations(CART_TS_INCREASE, "quantity")] == ["item.quantity++;"]
        assert [c["code"] for c in sig.computations(CART_TS_DECREASE, "quantity")] == ["item.quantity--;"]
        totals = [c["code"] for c in sig.computations(CART_TS_LOAD, "total")]
        assert totals == ["this.totalAmount = this.cartItems.reduce((sum, item) => sum + item.subtotal, 0);"]
        assert sig.computations(CHECKOUT_TOTAL, "total")[0]["code"] == "total_amount += (price * item.quantity)"
        assert sig.computations("    def get_subtotal(self):\n        return self.quantity * self.price", "subtotal")
        assert sig.delta_calls(CART_TS_INCREASE + CART_TS_DECREASE) == [("addToCart", 1), ("addToCart", -1)]
        assert sig.numeric_terms(TOTAL_Q) == ["subtotal", "quantity", "total"]


# ===========================================================================
# 1. CartItem call-site lookup
# ===========================================================================

def _coverage(complete=True, sites=None):
    sites = sites if sites is not None else [
        {"file": F_VIEWS, "line": 296, "symbol": "CartSyncView.get"},
        {"file": F_VIEWS, "line": 339, "symbol": "CartAddView.post"},
        {"file": F_VIEWS, "line": 366, "symbol": "CartRemoveView.post"},
        {"file": F_VIEWS, "line": 376, "symbol": "CartClearView.post"},
    ]
    return {"references": {"CartItem": {"identifier": "CartItem", "total_lines": len(sites), "indirect_lines": 1,
                                        "sites": sites, "complete": complete}}}


class TestCallSiteCoverage:
    EVIDENCE = lambda self: [SYNC(), ADD(), REMOVE(), CLEAR(),
                             item("E20", "backend/accounts/models.py", 7, "class CartItem(models.Model):\n    quantity = 1",
                                  "CartItem", None, "class")]

    def test_crud_question_uses_call_site_profile(self):
        assert EvidenceEvaluationService.evaluation_profile(CALL_SITE_Q, "symbol_lookup") == "call_site"
        assert EvidenceEvaluationService.evaluation_profile("Where is CartItem used?", "symbol_lookup") == "call_site"
        assert EvidenceEvaluationService.evaluation_profile("Where is CartItem defined?", "symbol_lookup") == "symbol_lookup"

    def test_complete_search_lists_crud_and_http_methods(self):
        result = evaluator().evaluate(CALL_SITE_Q, self.EVIDENCE(), "symbol_lookup", 0, 1, search_coverage=_coverage())
        assert result.sufficiency == "sufficient"
        note = next(n for n in result.grounding_notes if n.startswith("Repository-wide search for CartItem"))
        assert "exhaustive" in note and "NOT complete" not in note
        assert "CartSyncView.get [E8] read, GET handler" in note
        assert "CartAddView.post [E5] create/delete/update, POST handler" in note
        assert "CartClearView.post [E11] delete" in note
        assert "No PUT/PATCH/DELETE handler references CartItem" in note

    def test_truncated_or_unretrieved_sites_are_not_sufficient(self):
        truncated = evaluator().evaluate(CALL_SITE_Q, self.EVIDENCE(), "symbol_lookup", 1, 1,
                                         search_coverage=_coverage(complete=False))
        assert truncated.sufficiency == "partially_sufficient"
        assert any("result limit" in u for u in truncated.unverified_connections)

        sites = _coverage()["references"]["CartItem"]["sites"] + [
            {"file": F_ORDERS, "line": 705, "symbol": "RazorpayWebhookView._handle_payment_captured"}]
        missing = evaluator().evaluate(CALL_SITE_Q, self.EVIDENCE(), "symbol_lookup", 0, 1,
                                       search_coverage=_coverage(sites=sites))
        assert missing.sufficiency == "partially_sufficient"
        assert any("1 of 5 reference sites" in u for u in missing.unverified_connections)
        assert "RazorpayWebhookView" in missing.recommended_queries["symbol_queries"]

    def test_without_coverage_answer_must_not_claim_exhaustive(self):
        result = evaluator().evaluate(CALL_SITE_Q, self.EVIDENCE(), "symbol_lookup", 0, 1)
        assert any("Do not claim the list is exhaustive" in n for n in result.grounding_notes)
        text = "These are all the call sites of `CartItem`: `CartAddView` [E5] and `CartClearView` [E11]."
        res = CitationValidationService().validate_answer_citations(answer(text, self.EVIDENCE()), self.EVIDENCE())
        assert not res.is_valid
        assert any("exhaustive" in r for r in res.unsupported_claims)

    def test_exhaustive_claim_allowed_with_complete_retrieved_coverage(self):
        evidence = self.EVIDENCE()
        text = "These are all the call sites of `CartItem`: `CartSyncView` [E8], `CartAddView` [E5], " \
               "`CartRemoveView` [E10] and `CartClearView` [E11]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence,
                                                                    search_coverage=_coverage())
        assert res.is_valid, res.unsupported_claims


class TestReferenceSearchRetrieval:
    FILES = {
        "backend/accounts/views.py": "class CartAddView(APIView):\n    def post(self, request):\n"
                                     "        item, _ = CartItem.objects.get_or_create(user=u)\n        item.save()\n",
        "backend/orders/views.py": "def capture(order):\n    CartItem.objects.filter(user=order.user).delete()\n",
        "backend/accounts/admin.py": "admin.site.register(CartItem)\n",
        "backend/accounts/models.py": "class CartItem(models.Model):\n    pass\n",
        "frontend/src/services/cartservice.ts": "export interface CartItem { id: number }\nconst c: CartItem[] = [];\n",
    }

    def _service(self, tmp_path):
        from backend.app.services.retrieval_service import RetrievalService

        for rel, content in self.FILES.items():
            (tmp_path / rel).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / rel).write_text(content, encoding="utf-8")
        svc = RetrievalService(MagicMock(), MagicMock())
        symbols = {
            "backend/accounts/views.py": [SimpleNamespace(name="post", qualified_name="CartAddView.post",
                                                          symbol_type="method", start_line=2, end_line=4)],
            "backend/orders/views.py": [SimpleNamespace(name="capture", qualified_name="capture",
                                                        symbol_type="function", start_line=1, end_line=2)],
        }
        svc._file_symbols = lambda db, repo_id, path: symbols.get(path.replace("\\", "/"), []) if repo_id == 4 else []
        return svc

    def test_backend_reference_sites_and_complete_coverage(self, tmp_path):
        svc = self._service(tmp_path)
        contexts, cov = svc._reference_search(MagicMock(), 4, str(tmp_path), "CartItem", backend_only=True)
        assert {s["symbol"] for s in cov["sites"]} == {"CartAddView.post", "capture"}
        assert cov["complete"] and cov["indirect_lines"] == 1  # admin registration is not a runtime site
        assert all(not sig.is_frontend_runtime(c["file"]) for c in contexts)
        add = next(c for c in contexts if c.get("qualified_name") == "CartAddView.post")
        assert "item.save()" in add["source"]  # whole small method: create + update visible

    def test_truncation_marks_coverage_incomplete(self, tmp_path):
        svc = self._service(tmp_path)
        svc.REFERENCE_SEARCH_LIMIT = 2
        _, cov = svc._reference_search(MagicMock(), 4, str(tmp_path), "CartItem")
        assert not cov["complete"]

    def test_graph_profile_retrieval_records_coverage(self):
        retrieval = MagicMock()
        retrieval._reference_search.return_value = ([{"file": F_VIEWS}], {"identifier": "CartItem", "sites": [],
                                                                         "complete": True})
        graph = InvestigationGraphService.__new__(InvestigationGraphService)
        graph.retrieval_service = retrieval
        graph.evidence_evaluation_service = evaluator()
        contexts, cov = graph._profile_retrieval(MagicMock(), 4, "/repo", CALL_SITE_Q, "symbol_lookup", {})
        assert contexts == [{"file": F_VIEWS}]
        assert cov["references"]["CartItem"]["complete"]
        assert retrieval._reference_search.call_args.args[-1] is True  # "model" question -> backend only


# ===========================================================================
# 2. Payment capture
# ===========================================================================

class TestPaymentCaptureTrace:
    def test_named_function_uses_symbol_trace_profile(self):
        assert EvidenceEvaluationService.function_targets(PAYMENT_Q) == ["_handle_payment_captured"]
        assert EvidenceEvaluationService.evaluation_profile(PAYMENT_Q, "feature_flow") == "symbol_trace"

    def test_trace_requires_definition_and_caller_and_scopes_operations(self):
        result = evaluator().evaluate(PAYMENT_Q, [WEBHOOK(), CAPTURED(), CLEAR()], "feature_flow", 0, 1)
        assert result.sufficiency == "sufficient", result.missing_claims
        note = result.grounding_notes[0]
        assert "inside _handle_payment_captured [E13]" in note
        assert "delete CartItem: `CartItem.objects.filter(user=order.user).delete()` (line 689)" in note
        assert "create OrderStatusHistory" in note
        assert "OTHER functions/views are not part of _handle_payment_captured" in note
        assert stage(result, "callers").evidence_ids == ["E12"]
        assert "E11" not in sum((s.evidence_ids for s in result.flow_stages), [])  # CartClearView is unrelated

    def test_unrelated_cart_operations_do_not_make_it_sufficient(self):
        result = evaluator().evaluate(PAYMENT_Q, [CLEAR(), REMOVE(), ADD()], "feature_flow", 0, 1)
        assert result.sufficiency != "sufficient"
        assert "target_definition" in result.missing_stages
        assert "_handle_payment_captured" in result.recommended_queries["symbol_queries"]

    def test_missing_caller_is_partial(self):
        result = evaluator().evaluate(PAYMENT_Q, [CAPTURED()], "feature_flow", 0, 1)
        assert result.sufficiency == "partially_sufficient"
        assert "callers" in result.missing_stages
        assert "_handle_payment_captured" in result.recommended_queries["caller_queries"]

    def test_cart_deletion_attributed_from_other_view_is_unsupported(self):
        evidence = [WEBHOOK(), CAPTURED(), CLEAR()]
        bad = "`_handle_payment_captured` deletes the user's cart items [E11]."
        res = CitationValidationService().validate_answer_citations(answer(bad, evidence), evidence)
        assert not res.is_valid
        assert any("attributes a data change to _handle_payment_captured" in r for r in res.unsupported_claims)
        good = "`_handle_payment_captured` deletes the user's cart items with `CartItem.objects.filter` [E13]."
        assert CitationValidationService().validate_answer_citations(answer(good, evidence), evidence).is_valid


# ===========================================================================
# 3. Cart subtotal / quantity / total
# ===========================================================================

class TestCartCalculation:
    def test_calculation_profile_requires_each_quantity_and_price_fields(self):
        assert EvidenceEvaluationService.evaluation_profile(TOTAL_Q, "feature_flow") == "calculation"
        backend_only = evaluator().evaluate(TOTAL_Q, [ADD(), SYNC()], "feature_flow", 0, 1)
        assert backend_only.sufficiency != "sufficient"  # unrelated cart views do not compute subtotal/total
        assert {"computation_subtotal", "computation_total"} <= set(backend_only.missing_stages)

        full = evaluator().evaluate(TOTAL_Q, [UI_INC(), UI_DEC(), CART_LOAD(), CHECKOUT(), VARIANT()], "feature_flow", 0, 1)
        assert full.sufficiency == "sufficient", full.missing_claims
        assert stage(full, "source_fields").evidence_ids == ["E16"]

        no_fields = evaluator().evaluate(TOTAL_Q, [UI_INC(), UI_DEC(), CART_LOAD(), CHECKOUT()], "feature_flow", 0, 1)
        assert "source_fields" in no_fields.missing_stages
        assert "discounted_price =" in no_fields.recommended_queries["text_queries"]

    def test_frontend_and_backend_computations_are_kept_apart(self):
        result = evaluator().evaluate(TOTAL_Q, [UI_INC(), CART_LOAD(), CHECKOUT(), VARIANT()], "feature_flow", 0, 1)
        total = next(n for n in result.grounding_notes if n.startswith("Computation of total."))
        assert "FRONTEND: Cart.loadCart [E14]" in total and "BACKEND: CheckoutView.post [E15]" in total
        subtotal = next(n for n in result.grounding_notes if n.startswith("Computation of subtotal."))
        assert "BACKEND: none in the evidence (do not claim the backend does not compute it)" in subtotal
        # One fact per quantity, ahead of the generic notes, so all reach the answer prompt.
        assert [n.split(".")[0] for n in result.grounding_notes[:3]] == [
            "Computation of subtotal", "Computation of quantity", "Computation of total"]

    def test_complete_search_states_one_sided_absence(self):
        coverage = {"computations": {"total": {"complete": True, "frontend": [{"file": F_CART, "line": 77}],
                                               "backend": []}}}
        result = evaluator().evaluate("How is the cart total calculated?", [CART_LOAD()], "feature_flow", 0, 1,
                                      search_coverage=coverage)
        assert "BACKEND: none (a complete repository-wide search found no backend computation)" in result.grounding_notes[0]

    def test_repair_note_quoting_removed_claim_is_not_reflagged(self):
        evidence = [UI_INC(), CART_LOAD()]
        text = ("`increaseQty` increments `item.quantity` [E1]. "
                "This is the only place the repository shows quantity being persisted [E1].")
        validator = CitationValidationService()
        first = validator.validate_answer_citations(answer(text, evidence), evidence)
        assert not first.is_valid
        repaired = validator.repair_answer(answer(text, evidence), evidence, first)
        assert "Unverified statements removed" in repaired.answer and "the only place" in repaired.answer
        again = validator.validate_answer_citations(repaired, evidence)
        assert again.is_valid, again.unsupported_claims

    def test_absence_claim_contradicted_by_repository_wide_search(self):
        coverage = {"computations": {"subtotal": {"complete": True, "frontend": [{"file": F_CART, "line": 69}],
                                                  "backend": [{"file": "backend/orders/models.py", "line": 64}]}}}
        evidence = [CART_LOAD()]
        text = "The backend does not calculate the subtotal; it is computed only in `loadCart` [E14]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence,
                                                                    search_coverage=coverage)
        assert not res.is_valid
        assert any("backend/orders/models.py:64" in r for r in res.unsupported_claims)

    def test_absence_claim_needs_search_coverage(self):
        evidence = [CART_LOAD()]
        text = "The backend never recalculates the cart subtotal [E14]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence)
        assert not res.is_valid
        assert any("without search coverage" in r for r in res.unsupported_claims)
        scoped = "The retrieved evidence does not show a backend subtotal calculation."
        ok = CitationValidationService().validate_answer_citations(
            answer("`calculateTotal` sums `item.subtotal` into `totalAmount` [E14]. " + scoped, evidence), evidence)
        assert ok.is_valid, ok.unsupported_claims

    def test_local_never_about_cited_code_is_not_a_repository_claim(self, tmp_path):
        # Live run: this correct statement was removed because of "never".
        (tmp_path / "frontend/src/app/cart").mkdir(parents=True)
        (tmp_path / "frontend/src/app/cart/cart.html").write_text('<button (click)="decreaseQty(item)">-</button>')
        evidence = [UI_INC(), UI_DEC()]
        text = ("The `increaseQty` method increments `item.quantity` [E1] and the `decreaseQty` method decrements it, "
                "never below 1 [E2].")
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence,
                                                                    repository_path=str(tmp_path))
        # (cart.ts itself is absent from tmp_path, so only claim support is asserted here)
        assert res.unsupported_claims == [] and res.supported_claims
        scoped = "`decreaseQty` is not called anywhere else in the codebase [E2]."
        flagged = CitationValidationService().validate_answer_citations(answer(scoped, evidence), evidence,
                                                                        repository_path=str(tmp_path))
        assert any("cart.html:1" in r for r in flagged.unsupported_claims)

    def test_absence_of_identifier_checked_against_repository(self, tmp_path):
        (tmp_path / "backend/orders").mkdir(parents=True)
        (tmp_path / "backend/orders/models.py").write_text(
            "class OrderItem(models.Model):\n    def get_subtotal(self):\n        return self.quantity * self.price\n")
        evidence = [CART_LOAD()]
        text = "There is no `get_subtotal` method anywhere in the backend [E14]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence,
                                                                    repository_path=str(tmp_path))
        assert any("backend/orders/models.py:2" in r for r in res.unsupported_claims)


# ===========================================================================
# 4. Authenticated cart retrieval and quantity update (complete flows)
# ===========================================================================

class TestAuthenticatedCartFlows:
    def test_quantity_update_flow_verified_with_discarded_response(self):
        evidence = [UI_INC(), UI_DEC(), CLIENT_ADD(), ROUTES(), ADD()]
        result = evaluator().evaluate(UPDATE_Q, evidence, "feature_flow", 0, 1)
        assert result.sufficiency == "sufficient", (result.missing_stages, result.unverified_connections)
        names = [s.stage for s in result.flow_stages]
        assert names[:7] == ["ui_event_handler", "client_service", "url_routing", "backend_handler", "persistence",
                             "backend_response", "frontend_handling"]
        handling = stage(result, "frontend_handling")
        assert not handling.present and handling.verified_absent and "NOT consumed" in handling.details
        assert stage(result, "authentication_check").present
        notes = "\n".join(result.grounding_notes)
        assert "+1 (increment) to addToCart" in notes and "-1 (decrement) to addToCart" in notes
        assert "`cart_item.quantity += quantity` [E5]" in notes

    def test_actor_questions_classified_feature_flow_use_flow_profile(self):
        # Live Groq run classified the retrieval question as feature_flow; it must
        # still be verified as a complete flow, not as a code explanation.
        assert EvidenceEvaluationService.evaluation_profile(RETRIEVE_Q, "feature_flow") == "flow"
        assert EvidenceEvaluationService.evaluation_profile(UPDATE_Q, "feature_flow") == "flow"
        view_only = evaluator().evaluate(RETRIEVE_Q, [SYNC(), item("E1", F_VIEWS, 295, SYNC_VIEW.split("\n\n")[1], "get")],
                                         "feature_flow", 0, 1)
        assert view_only.sufficiency == "partially_sufficient"
        assert {"client_service", "frontend_handling"} <= set(view_only.missing_stages)

    def test_retrieval_flow_requires_consumer_serializer_and_auth(self):
        evidence = [RESTORE(), CLIENT_LOAD(), ROUTES(), SYNC(), SER()]
        result = evaluator().evaluate(RETRIEVE_Q, evidence, "end_to_end_flow", 0, 1)
        assert result.sufficiency == "sufficient", (result.missing_stages, result.unverified_connections)
        assert stage(result, "ui_event_handler").evidence_ids == ["E7"]  # session restore in auth service
        assert stage(result, "frontend_handling").evidence_ids == ["E6"]
        assert stage(result, "persistence").present  # CartSyncView.get reads CartItem

    def test_retrieval_without_serializer_or_auth_is_partial(self):
        without_serializer = evaluator().evaluate(RETRIEVE_Q, [RESTORE(), CLIENT_LOAD(), ROUTES(), SYNC()],
                                                  "end_to_end_flow", 0, 1)
        assert "serialization" in without_serializer.missing_stages
        assert "CartItemSerializer" in without_serializer.recommended_queries["symbol_queries"]
        no_auth = dict(SYNC(), snippet=SYNC_VIEW_NO_AUTH)
        result = evaluator().evaluate(RETRIEVE_Q, [RESTORE(), CLIENT_LOAD(), ROUTES(), no_auth, SER()],
                                      "end_to_end_flow", 0, 1)
        assert "authentication_check" in result.missing_stages

    def test_truncated_callback_is_not_proof_of_discarded_response(self):
        # Live run: a window ending at `.subscribe(dbCart => {` was read as "not consumed".
        cut = "\n".join(SVC_LOAD.split("\n")[:3])
        assert cut.rstrip().endswith("{")
        assert sig.response_consumption(cut, sig.extract_http_calls(cut)[0]["position"]) == "unknown"
        assert not sig.method_is_complete(cut, "loadFromDB") and sig.method_is_complete(SVC_LOAD, "loadFromDB")
        truncated = dict(CLIENT_LOAD(), snippet=cut, end_line=47)
        result = evaluator().evaluate(RETRIEVE_Q, [RESTORE(), truncated, ROUTES(), SYNC(), SER()], "end_to_end_flow", 0, 1)
        handling = stage(result, "frontend_handling")
        assert not handling.present and not handling.verified_absent
        assert result.sufficiency == "partially_sufficient"

    def test_stage_evidence_is_interleaved_for_the_answer_prompt(self):
        evaluation = {"flow_stages": [{"evidence_ids": ["E1", "E2", "E3", "E4"]}, {"evidence_ids": ["E9"]},
                                      {"evidence_ids": ["E7", "E8"]}]}
        evidence = [{"evidence_id": f"E{i}", "file": "x.py", "relevance_score": 0.5} for i in range(1, 11)]
        ordered = [e["evidence_id"] for e in InvestigationGraphService._order_evidence_for_answer(evidence, evaluation)]
        assert ordered[:5] == ["E1", "E9", "E7", "E2", "E8"]

    def test_unconsumed_response_is_not_frontend_handling(self):
        discarded = dict(CLIENT_LOAD(), snippet=SVC_LOAD_DISCARDED)
        result = evaluator().evaluate(RETRIEVE_Q, [RESTORE(), discarded, ROUTES(), SYNC(), SER()], "end_to_end_flow", 0, 1)
        handling = stage(result, "frontend_handling")
        assert not handling.present and handling.verified_absent

    def test_missing_frontend_trigger_is_partial(self):
        result = evaluator().evaluate(UPDATE_Q, [CLIENT_ADD(), ROUTES(), ADD()], "feature_flow", 0, 1)
        assert result.sufficiency == "partially_sufficient"
        assert "ui_event_handler" in result.missing_stages
        assert "addToCart" in result.recommended_queries["caller_queries"]

    def test_unrelated_handler_evidence_is_not_sufficient(self):
        # Remove/clear views touch CartItem but are not the quantity-update flow.
        result = evaluator().evaluate(UPDATE_Q, [UI_INC(), CLIENT_ADD(), ROUTES(), REMOVE(), CLEAR()], "feature_flow", 0, 1)
        assert result.sufficiency != "sufficient"
        assert "backend_handler" in result.missing_stages
        assert "CartAddView" in result.recommended_queries["symbol_queries"]

    def test_handler_persistence_is_scoped_to_the_requested_http_method(self):
        get_only = SYNC_VIEW.split("    def post")[0].rstrip()
        post_only = dict(SYNC(), snippet=SYNC_VIEW.replace(
            "        cart_items = CartItem.objects.filter(user=request.user)\n", ""))
        result = evaluator().evaluate(RETRIEVE_Q, [RESTORE(), CLIENT_LOAD(), ROUTES(), post_only, SER()], "end_to_end_flow", 0, 1)
        # Only CartSyncView.post touches the ORM now; the GET flow has no read.
        assert "persistence" in result.missing_stages
        assert get_only  # sanity

    def test_partial_client_window_gets_completed_by_retrieval(self, tmp_path):
        from backend.app.services.retrieval_service import RetrievalService

        rel = "frontend/src/services/cartservice.ts"
        (tmp_path / rel).parent.mkdir(parents=True)
        (tmp_path / rel).write_text("export class Cartservice {\n" + SVC_ADD + "\n}\n", encoding="utf-8")
        svc = RetrievalService(MagicMock(), MagicMock())
        svc._file_symbols = lambda db, repo_id, path: [SimpleNamespace(
            name="addToCart", qualified_name="Cartservice.addToCart", symbol_type="method", start_line=2, end_line=15)]
        window = item("E30", rel, 12, "\n".join(SVC_ADD.split("\n")[10:13]))
        assert sig.extract_http_calls(window["snippet"])[0]["enclosing"] is None
        out = svc._complete_client_methods(MagicMock(), 4, str(tmp_path), [window], UPDATE_Q)
        assert out and out[0]["qualified_name"] == "Cartservice.addToCart" and "addToCart(productId" in out[0]["source"]


# ===========================================================================
# 5. Validator: inline citations, increments, negative claims
# ===========================================================================

class TestValidatorFalsePositives:
    def test_increment_decrement_wording_matches_plus_minus_one(self):
        evidence = [UI_INC(), UI_DEC()]
        text = "Clicking plus increments the quantity locally [E1]; clicking minus decrements it [E2]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence)
        assert res.is_valid, res.unsupported_claims

    def test_increment_claim_citing_unrelated_code_is_unsupported(self):
        evidence = [UI_INC(), CLEAR()]
        text = "Clicking plus increments the quantity locally [E11]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence)
        assert not res.is_valid

    def test_every_inline_id_must_exist_and_be_cited(self):
        evidence = [UI_INC()]
        text = "`increaseQty` calls `addToCart` with 1 [E1] and the backend saves it [E99]."
        res = CitationValidationService().validate_answer_citations(answer(text, evidence), evidence)
        assert not res.is_valid and "E99" in res.hallucinated_ids


# ===========================================================================
# Repository isolation and repository-specific Qdrant deletion
# ===========================================================================

class TestQdrantRepositoryDeletion:
    def test_deleting_one_repository_never_deletes_another(self):
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, FieldCondition, Filter, MatchValue, PointStruct, VectorParams

        from backend.app.services.vector_service import VectorService

        svc = VectorService.__new__(VectorService)
        svc.client = QdrantClient(":memory:")
        svc.client.create_collection(svc.COLLECTION_NAME, vectors_config=VectorParams(size=4, distance=Distance.COSINE))
        points, pid = [], 0
        for repo_id, count in ((4, 120), (5, 80), (14, 30)):  # 14 shares a digit prefix with 4
            for _ in range(count):
                pid += 1
                points.append(PointStruct(id=pid, vector=[1.0, 0.1 * (pid % 7), 0.0, 0.0], payload={"repository_id": repo_id}))
        svc.upsert(points)

        def count(repo_id):
            flt = Filter(must=[FieldCondition(key="repository_id", match=MatchValue(value=repo_id))])
            return svc.client.count(svc.COLLECTION_NAME, count_filter=flt, exact=True).count

        svc.delete_repository(4)
        assert (count(4), count(5), count(14)) == (0, 80, 30)
        assert svc.search([1.0, 0.0, 0.0, 0.0], repository_id=4) == []
        assert all(p.payload["repository_id"] == 5 for p in svc.search([1.0, 0.0, 0.0, 0.0], repository_id=5, limit=50))

        svc.delete_repository(99)  # unknown repository: nothing else touched
        assert (count(5), count(14)) == (80, 30)
