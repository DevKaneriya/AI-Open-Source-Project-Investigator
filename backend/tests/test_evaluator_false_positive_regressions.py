"""
Regressions for three evaluator false positives from live Crumbs runs:

1. Registration: ``Auth.register`` reads the response in ``tap`` and the
   component's ``next`` callback navigates on success; the evaluator called it
   a fire-and-forget subscription, contradicting the (correct) answer.
2. Redis/cache: cache-backend and Celery-broker configuration alone was
   marked sufficient for a question about actual cache usage.
3. Guest cart -> checkout: a verified guest-cart merge was marked sufficient
   for a flow that must reach checkout, while the answer said checkout was
   not verified.

Evidence snippets mirror the real Crumbs code. No network or LLM calls.
"""

from unittest.mock import MagicMock

from backend.app.services import evidence_signals as sig
from backend.app.services.answer_generation_service import AnswerGenerationService
from backend.app.services.evidence_evaluation_service import EvidenceEvaluationService


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


def stage(result, name):
    return next(s for s in result.flow_stages if s.stage == name)


def construct(question, evidence, evaluation, confidence="high", limitations=None):
    """Run the answer-construction checks against an evaluation result."""
    svc = AnswerGenerationService(MagicMock())
    ids = [e["evidence_id"] for e in evidence]
    return svc._validate_and_construct_answer(
        question=question,
        llm_response={
            "answer": " ".join(f"Step [{eid}]." for eid in ids),
            "cited_evidence_ids": ids,
            "confidence": confidence,
            "limitations": list(limitations or []),
        },
        supplied_evidence=evidence,
        evaluation_context=evaluation.model_dump(),
    )


# ===========================================================================
# 1. Registration response handling
# ===========================================================================

REGISTER_COMPONENT = """\
  register() {
    this.isSubmitting = true;
    this.errorMessage = '';
    this.successMessage = '';
    this.auth.register({
      first_name: this.firstName,
      last_name: this.lastName,
      email: this.email,
      password: this.password
    }).subscribe({
      next: () => {
        this.isSubmitting = false;
        this.successMessage = 'Account created successfully. Redirecting to your account...';
        this.router.navigate(['/account'], { replaceUrl: true });
      },
      error: () => {
        this.isSubmitting = false;
        this.errorMessage = 'Registration failed. Please check your details and try again.';
      }
    });
  }"""

AUTH_REGISTER = """\
  register(data: { first_name: string; last_name: string; email: string; password: string }) {
    return this.http.post<AuthResponse>(
      `${environment.apiUrl}/accounts/register/`,
      data,
      { withCredentials: true }
    ).pipe(
      tap(res => this.setCurrentUser(res.user, true)),
    );
  }"""

AUTH_REGISTER_PLAIN = """\
  register(data: { first_name: string; last_name: string; email: string; password: string }) {
    return this.http.post<AuthResponse>(`${environment.apiUrl}/accounts/register/`, data, { withCredentials: true });
  }"""

REGISTER_COMPONENT_DISCARDED = """\
  register() {
    this.isSubmitting = true;
    this.auth.register({ first_name: this.firstName, last_name: this.lastName, email: this.email, password: this.password }).subscribe();
    this.router.navigate(['/account']);
  }"""

ACCOUNTS_URLS = """\
    path('register/', RegisterView.as_view(), name='register'),
    path('login/', LoginView.as_view(), name='login'),
    path('cart/sync/', CartSyncView.as_view(), name='cart_sync'),
    path('cart/add/', CartAddView.as_view(), name='cart_add'),"""

REGISTER_VIEW = """\
class RegisterView(APIView):
    permission_classes = [AllowAny]

    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = serializer.save()
        refresh = RefreshToken.for_user(user)
        response = Response({'user': _user_payload(user)}, status=status.HTTP_201_CREATED)
        _set_auth_cookies(response, str(refresh.access_token), str(refresh))
        return response"""

REGISTER_SERIALIZER = """\
class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)

    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'email', 'password')"""

F_REGISTER = "frontend/src/app/register/register.ts"
F_AUTH = "frontend/src/services/auth.ts"
F_ACC_URLS = "backend/accounts/urls.py"
F_ACC_VIEWS = "backend/accounts/views.py"

REGISTER_Q = ("How does the Angular registration component submit the form to the backend, "
              "and how is the registration response consumed on the frontend?")


def registration_evidence(component=REGISTER_COMPONENT, service=AUTH_REGISTER):
    return [
        item("E4", F_REGISTER, 32, component, "register", "Register.register"),
        item("E8", F_AUTH, 83, service, "register", "Auth.register"),
        item("E1", F_ACC_URLS, 26, ACCOUNTS_URLS, "", None, ""),
        item("E5", F_ACC_VIEWS, 83, REGISTER_VIEW, "RegisterView", None, "class"),
        item("E6", "backend/accounts/serializers.py", 15, REGISTER_SERIALIZER, "RegisterSerializer", None, "class"),
    ]


class TestRegistrationResponseHandling:
    def test_tap_in_returned_pipe_reads_the_response(self):
        call = sig.extract_http_calls(AUTH_REGISTER)[0]
        assert sig.response_consumption(AUTH_REGISTER, call["position"]) == "consumed"
        plain = sig.extract_http_calls(AUTH_REGISTER_PLAIN)[0]
        assert sig.response_consumption(AUTH_REGISTER_PLAIN, plain["position"]) == "returned"

    def test_success_callback_without_parameter_is_not_fire_and_forget(self):
        outcomes = {sig.response_consumption(REGISTER_COMPONENT, p)
                    for p in sig.member_call_positions(REGISTER_COMPONENT, "register")}
        assert outcomes == {"acknowledged"}
        # Logging-only and empty callbacks still discard the response.
        for body in ("() => console.log('done')", "() => {}", "res => console.log('done')"):
            code = f"  ping() {{\n    this.http.get(`${{api}}/ping/`).subscribe({body});\n  }}"
            assert sig.response_consumption(code, sig.extract_http_calls(code)[0]["position"]) == "ignored", body

    def test_evaluator_agrees_response_is_consumed(self):
        result = evaluator().evaluate(REGISTER_Q, registration_evidence(), "feature_flow", 0, 1)
        handling = stage(result, "frontend_handling")
        assert handling.present and not handling.verified_absent
        assert set(handling.evidence_ids) == {"E8", "E4"}
        assert "Auth.register" in handling.details or "auth.ts" in handling.details
        assert "success callback runs when the response arrives" in handling.details
        everything = " ".join([handling.details, *result.grounding_notes, *result.missing_claims])
        assert "NOT consumed" not in everything and "fire-and-forget" not in everything
        assert result.sufficiency == "sufficient", (result.missing_stages, result.unverified_connections)

    def test_component_next_callback_alone_counts_as_handling(self):
        # Service returns the observable untouched; the component reacts on success.
        result = evaluator().evaluate(REGISTER_Q, registration_evidence(service=AUTH_REGISTER_PLAIN),
                                      "feature_flow", 0, 1)
        handling = stage(result, "frontend_handling")
        assert handling.present and not handling.verified_absent and handling.evidence_ids == ["E4"]

    def test_real_fire_and_forget_is_still_detected(self):
        result = evaluator().evaluate(
            REGISTER_Q,
            registration_evidence(component=REGISTER_COMPONENT_DISCARDED, service=AUTH_REGISTER_PLAIN),
            "feature_flow", 0, 1,
        )
        handling = stage(result, "frontend_handling")
        assert not handling.present and handling.verified_absent and "NOT consumed" in handling.details

    def test_answer_prompt_carries_the_same_verdict(self):
        result = evaluator().evaluate(REGISTER_Q, registration_evidence(), "feature_flow", 0, 1)
        prompt = AnswerGenerationService(MagicMock())._build_system_prompt(
            "feature_flow", "Crumbs", result.model_dump())
        handling_fact = next(line for line in prompt.splitlines() if line.startswith("- frontend_handling:"))
        assert "Frontend code consuming the response" in handling_fact and "auth.ts" in handling_fact
        assert "success callback runs when the response arrives" in handling_fact and "register.ts" in handling_fact
        assert "NOT consumed" not in prompt and "fire-and-forget" not in prompt


# ===========================================================================
# 2. Redis / cache sufficiency
# ===========================================================================

SETTINGS_CACHES = """\
REDIS_URL = os.environ.get('REDIS_URL', '')

if REDIS_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "CONNECTION_POOL_KWARGS": {"protocol": 2},
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        }
    }"""

SETTINGS_CELERY = """\
CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.environ.get('CELERY_RESULT_BACKEND', 'redis://localhost:6379/0')
CELERY_ACCEPT_CONTENT = ['application/json']
CELERY_TASK_SERIALIZER = 'json'"""

CATALOG_VIEWS = """\
class ProductListView(generics.ListAPIView):
    serializer_class = ProductSerializer

    @method_decorator(cache_page(60 * 15))
    def get(self, request, *args, **kwargs):
        return super().get(request, *args, **kwargs)"""

F_SETTINGS = "backend/main/settings.py"
REDIS_Q = ("How is Redis used for caching in this application? What data is cached, what TTL and cache keys "
           "are used, and how is the cache invalidated or bypassed?")


def settings_evidence():
    return [
        item("E1", F_SETTINGS, 316, SETTINGS_CACHES, "", None, ""),
        item("E2", F_SETTINGS, 290, SETTINGS_CELERY, "", None, ""),
    ]


class TestCacheSufficiency:
    def test_cache_questions_use_the_deterministic_cache_profile(self):
        for q_type in ("cross_cutting_concern", "feature_flow", "architectural_overview", None):
            assert EvidenceEvaluationService.evaluation_profile(REDIS_Q, q_type) == "cache"
        # Redis as a task broker is not a caching question.
        assert EvidenceEvaluationService.evaluation_profile(
            "How is Redis configured as the Celery broker?", "cross_cutting_concern") != "cache"

    def test_configuration_alone_is_not_sufficient(self):
        result = evaluator().evaluate(REDIS_Q, settings_evidence(), "cross_cutting_concern", 0, 2)
        assert result.sufficiency == "partially_sufficient"
        assert stage(result, "cache_configuration").present
        assert {"cache_operations", "cache_ttl", "cache_keys", "cache_invalidation", "cache_bypass"} <= set(
            result.missing_stages)
        assert result.retrieval_needed and "cache_page" in result.recommended_queries["text_queries"]
        notes = " ".join(result.grounding_notes)
        assert "Only cache/broker configuration was retrieved" in notes
        assert "not caching" in notes  # Celery broker is task queueing

    def test_no_evidence_of_cache_at_all_is_insufficient(self):
        result = evaluator().evaluate(REDIS_Q, [settings_evidence()[1]], "cross_cutting_concern", 0, 2)
        assert result.sufficiency == "insufficient"

    def test_verified_cache_usage_is_reported_per_aspect(self):
        evidence = settings_evidence() + [
            item("E3", "backend/catalog/views.py", 28, CATALOG_VIEWS, "ProductListView", None, "class")]
        result = evaluator().evaluate(REDIS_Q, evidence, "cross_cutting_concern", 2, 2)
        for name in ("cache_configuration", "cache_operations", "cache_ttl", "cache_keys"):
            assert stage(result, name).present, name
        assert "cache_page" in stage(result, "cache_operations").details
        # Nothing invalidates or bypasses the cache in the retrieved code.
        assert {"cache_invalidation", "cache_bypass"} <= set(result.missing_stages)
        assert result.sufficiency == "partially_sufficient"

    def test_answer_cannot_be_high_confidence_on_configuration_only(self):
        evidence = settings_evidence()
        result = evaluator().evaluate(REDIS_Q, evidence, "cross_cutting_concern", 2, 2)
        answer = construct(REDIS_Q, evidence, result)
        assert answer.confidence == "partial"
        assert "Missing flow stage: cache_operations" in answer.limitations


# ===========================================================================
# 3. Guest cart -> checkout
# ===========================================================================

AUTH_SET_USER = """\
  setCurrentUser(user: User, syncLocal: boolean) {
    this.currentUser.set(user);
    localStorage.setItem('auth_user', JSON.stringify(user));
    if (syncLocal) {
      // Fresh login: merge guest local items into DB
      this.cartService.syncCart();
    } else {
      this.cartService.loadFromDB();
    }
  }"""

CART_SYNC_CLIENT = """\
  syncCart() {
    const localItems = this.getLocalCart();
    this.http.post<CartItem[]>(`${environment.apiUrl}/accounts/cart/sync/`, { items: localItems }, { withCredentials: true })
      .subscribe(mergedCart => {
        this.cart.set(mergedCart);
        this.saveToLocal(mergedCart);
      });
  }"""

CART_SYNC_VIEW = """\
class CartSyncView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        \"\"\"Merge the guest (localStorage) cart into the user's DB cart.\"\"\"
        user = request.user
        for entry in request.data.get('items', []):
            try:
                product = Product.objects.get(id=entry.get('productId'))
                cart_item, created = CartItem.objects.get_or_create(
                    user=user, product=product, variant=entry.get('variant'),
                    defaults={'quantity': entry.get('quantity', 1)})
                if not created:
                    cart_item.quantity += entry.get('quantity', 1)
                    cart_item.save()
            except Product.DoesNotExist:
                continue

        cart_items = CartItem.objects.filter(user=user)
        serializer = CartItemSerializer(cart_items, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)"""

CART_SERIALIZER = """\
class CartItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = CartItem
        fields = ['id', 'productId', 'variant', 'quantity']"""

CHECKOUT_VIEW = """\
class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        cart_items = CartItem.objects.filter(user=request.user)
        if not cart_items.exists():
            return Response({'error': 'Cart is empty.'}, status=status.HTTP_400_BAD_REQUEST)
        order = Order.objects.create(user=request.user, total_amount=0, payment_method='COD')
        cart_items.delete()
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)"""

CHECKOUT_UNRELATED = """\
class CheckoutView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({'payment_methods': ['COD', 'Razorpay']})"""

F_CARTSVC = "frontend/src/services/cartservice.ts"
GUEST_Q = "Trace how a guest user's cart is merged into their account cart on login and then carried through to checkout."


def merge_evidence():
    return [
        item("E7", F_AUTH, 222, AUTH_SET_USER, "setCurrentUser", "Auth.setCurrentUser"),
        item("E6", F_CARTSVC, 35, CART_SYNC_CLIENT, "syncCart", "Cartservice.syncCart"),
        item("E3", F_ACC_URLS, 34, ACCOUNTS_URLS, "", None, ""),
        item("E2", F_ACC_VIEWS, 292, CART_SYNC_VIEW, "CartSyncView", None, "class"),
        item("E11", "backend/accounts/serializers.py", 133, CART_SERIALIZER, "CartItemSerializer", None, "class"),
    ]


def checkout_item(snippet=CHECKOUT_VIEW):
    return item("E20", "backend/orders/views.py", 32, snippet, "CheckoutView", None, "class")


class TestGuestCartToCheckout:
    def test_destination_is_read_from_the_question(self):
        assert sig.flow_destinations(GUEST_Q) == ["checkout"]
        assert sig.flow_destinations("Explain the guest-to-checkout flow.") == ["checkout"]
        # Ordinary flows are unchanged.
        for q in ("Trace how the guest cart is merged into the user cart on login.",
                  "Trace the checkout flow end-to-end from the Angular UI to the Django backend.",
                  "How does an authenticated user update the quantity of a cart item?"):
            assert sig.flow_destinations(q) == [], q

    def test_verified_merge_alone_is_not_sufficient(self):
        result = evaluator().evaluate(GUEST_Q, merge_evidence(), "feature_flow", 0, 2)
        # The merge itself is fully verified ...
        for name in ("ui_event_handler", "client_service", "url_routing", "backend_handler", "frontend_handling"):
            assert stage(result, name).present, name
        # ... but the requested flow does not reach checkout.
        assert result.sufficiency == "partially_sufficient"
        assert "checkout" in result.missing_stages
        assert not stage(result, "checkout").present
        assert "CheckoutView" in result.recommended_queries["symbol_queries"]
        assert result.retrieval_needed

    def test_answer_and_evaluation_agree_checkout_is_unverified(self):
        evidence = merge_evidence()
        result = evaluator().evaluate(GUEST_Q, evidence, "feature_flow", 2, 2)
        assert result.sufficiency != "sufficient"
        answer = construct(GUEST_Q, evidence, result,
                           limitations=["Checkout behaviour is not verified in the retrieved evidence."])
        assert answer.confidence == "partial"
        # The evaluator's verdict reaches the answer, not just the LLM's own wording.
        assert "Missing flow stage: checkout" in answer.limitations

    def test_checkout_that_reads_the_cart_completes_the_flow(self):
        result = evaluator().evaluate(GUEST_Q, merge_evidence() + [checkout_item()], "feature_flow", 0, 2)
        checkout = stage(result, "checkout")
        assert checkout.present and checkout.evidence_ids == ["E20"]
        assert result.sufficiency == "sufficient", (result.missing_stages, result.unverified_connections)

    def test_checkout_code_that_ignores_the_cart_is_unverified_hand_off(self):
        result = evaluator().evaluate(GUEST_Q, merge_evidence() + [checkout_item(CHECKOUT_UNRELATED)],
                                      "feature_flow", 0, 2)
        assert stage(result, "checkout").present
        assert any("hand-off to checkout is not verified" in u for u in result.unverified_connections)
        assert result.sufficiency == "partially_sufficient"


# ===========================================================================
# Cross-component consistency
# ===========================================================================

class TestNoContradictions:
    def test_partial_llm_evaluation_without_stages_caps_confidence(self):
        evidence = [item("E1", F_SETTINGS, 316, SETTINGS_CACHES, "", None, "")]
        evaluation = MagicMock()
        evaluation.model_dump.return_value = {
            "sufficiency": "partially_sufficient", "missing_stages": [], "unverified_connections": [],
            "flow_stages": [], "grounding_notes": [],
        }
        answer = construct("Explain the deployment configuration.", evidence, evaluation)
        assert answer.confidence == "partial"
        assert any("partially sufficient" in lim for lim in answer.limitations)

    def test_sufficient_evaluation_leaves_confidence_alone(self):
        evidence = registration_evidence()
        result = evaluator().evaluate(REGISTER_Q, evidence, "feature_flow", 0, 1)
        assert result.sufficiency == "sufficient"
        answer = construct(REGISTER_Q, evidence, result)
        assert answer.confidence == "high" and answer.limitations == []
