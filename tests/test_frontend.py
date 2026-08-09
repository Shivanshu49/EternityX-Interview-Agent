"""The chat UI is served by the same app, so the deployed URL is the demo."""

import re


def test_root_serves_the_chat_ui(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "EternityX Interview Agent" in response.text


def test_the_api_is_not_shadowed_by_the_static_mount(client):
    """The catch-all mount at / must not swallow /api/*."""
    response = client.post("/api/interview", json={})
    assert response.status_code == 422, "422 means FastAPI validated it, not a 404 from static"


def test_separately_hosted_frontend_can_preflight_the_api(client):
    response = client.options(
        "/api/interview?explain=1",
        headers={
            "Origin": "https://example.vercel.app",
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "*"
    assert "POST" in response.headers["access-control-allow-methods"]


def test_unknown_paths_still_404(client):
    assert client.get("/definitely-not-a-page").status_code == 404


def test_ui_posts_to_the_real_endpoint_and_handles_all_three_shapes():
    """Guard against the page drifting from the API contract."""
    page = open("frontend/index.html", encoding="utf-8").read()
    assert '"/api/interview"' in page
    for field in ("sessionId", "candidate", "message", "reply", "done", "feedback"):
        assert field in page, f"UI never references {field!r}"
    for section in ("summary", "strengths", "gaps", "next"):
        assert section in page, f"UI cannot render feedback.{section}"


def test_ui_has_no_external_dependencies():
    """No CDN, no build step -- it must work from a cold clone offline."""
    page = open("frontend/index.html", encoding="utf-8").read()
    remote = re.findall(r'(?:src|href)\s*=\s*["\'](https?:)?//[^"\']+', page)
    assert not remote, f"external resource(s) referenced: {remote}"
