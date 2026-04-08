from flux.security.identity import FluxIdentity, ANONYMOUS


class TestFluxIdentity:
    def test_create_identity(self):
        identity = FluxIdentity(
            subject="alice@acme.com",
            roles=frozenset({"operator"}),
        )
        assert identity.subject == "alice@acme.com"
        assert "operator" in identity.roles

    def test_identity_is_frozen(self):
        identity = FluxIdentity(subject="alice@acme.com")
        try:
            identity.subject = "bob@acme.com"
            assert False, "Should not be mutable"
        except AttributeError:
            pass

    def test_has_role(self):
        identity = FluxIdentity(
            subject="alice@acme.com",
            roles=frozenset({"operator", "viewer"}),
        )
        assert identity.has_role("operator") is True
        assert identity.has_role("admin") is False

    def test_anonymous_identity(self):
        assert ANONYMOUS.subject == "anonymous"
        assert "admin" in ANONYMOUS.roles

    def test_identity_metadata(self):
        identity = FluxIdentity(
            subject="alice@acme.com",
            metadata={"email": "alice@acme.com", "token_type": "oidc"},
        )
        assert identity.metadata["email"] == "alice@acme.com"


class TestWildcardMatching:
    def test_exact_match(self):
        identity = FluxIdentity(subject="alice@acme.com", roles=frozenset())
        assert identity.has_permission("workflow:report:run", {"workflow:report:run"}) is True

    def test_no_match(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert identity.has_permission("workflow:report:run", {"workflow:other:run"}) is False

    def test_star_matches_all(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert identity.has_permission("workflow:report:task:load:execute", {"*"}) is True

    def test_wildcard_at_end(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert (
            identity.has_permission("workflow:report:task:load:execute", {"workflow:report:*"})
            is True
        )

    def test_wildcard_partial_path(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert identity.has_permission("workflow:report:task:load:execute", {"workflow:*"}) is True

    def test_wildcard_in_middle(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert (
            identity.has_permission(
                "workflow:report:task:load:execute",
                {"workflow:report:task:*:execute"},
            )
            is True
        )

    def test_wildcard_no_match_different_prefix(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert identity.has_permission("workflow:report:run", {"admin:*"}) is False

    def test_multiple_permissions_any_match(self):
        identity = FluxIdentity(subject="alice@acme.com")
        assert (
            identity.has_permission(
                "workflow:report:run",
                {"workflow:other:run", "workflow:report:*"},
            )
            is True
        )
