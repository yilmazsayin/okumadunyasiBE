"""CORS remains explicit for production, local development, and previews."""

import importlib

import pytest

ALLOWED_ORIGINS = [
    "https://okumadunyasi.vercel.app",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:3000",
    "http://127.0.0.1:5173",
]


def preflight(client, origin):
    return client.options(
        "/upload",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )


@pytest.mark.parametrize("origin", ALLOWED_ORIGINS)
def test_allowed_origins_receive_exact_cors_header(client, origin):
    response = preflight(client, origin)
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert response.headers.get("Access-Control-Allow-Credentials") != "true"


@pytest.mark.parametrize(
    "origin",
    [
        "https://attacker.example",
        "https://okumadunyasi.vercel.app.attacker.example",
        "http://okumadunyasi.vercel.app",
        "https://arbitrary-preview.vercel.app",
        "null",
    ],
)
def test_unconfigured_origins_do_not_receive_cors_permission(client, origin):
    response = preflight(client, origin)
    assert response.status_code == 200
    assert "Access-Control-Allow-Origin" not in response.headers


def test_upload_preflight_supports_post_and_content_type(client):
    origin = "https://okumadunyasi.vercel.app"
    response = preflight(client, origin)
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == origin
    assert "POST" in response.headers["Access-Control-Allow-Methods"]
    assert "content-type" in response.headers["Access-Control-Allow-Headers"].lower()


def test_json_errors_are_visible_to_allowed_frontend(client):
    origin = "https://okumadunyasi.vercel.app"
    response = client.post("/upload", headers={"Origin": origin})
    assert response.status_code == 400
    assert response.get_json().get("error")
    assert response.headers["Access-Control-Allow-Origin"] == origin


def test_explicit_preview_origins_can_be_added_without_allowing_wildcards(monkeypatch):
    import app as module

    try:
        with monkeypatch.context() as environment:
            environment.setenv(
                "CORS_ORIGINS",
                "https://preview-one.example, https://preview-two.example, http://[::1]:5173",
            )
            configured = importlib.reload(module).app.test_client()
            for origin in (
                "https://preview-one.example",
                "https://preview-two.example",
                "https://okumadunyasi.vercel.app",
                "http://[::1]:5173",
            ):
                response = preflight(configured, origin)
                assert response.headers["Access-Control-Allow-Origin"] == origin
            response = preflight(configured, "https://unconfigured-preview.example")
            assert "Access-Control-Allow-Origin" not in response.headers
            response = preflight(configured, "https://preview-oneXexample")
            assert "Access-Control-Allow-Origin" not in response.headers
    finally:
        importlib.reload(module)


@pytest.mark.parametrize(
    "origin",
    [
        "*",
        "https://*.vercel.app",
        "https://preview.example/path",
        "file://preview",
        "https://u:p@example.com",
    ],
)
def test_invalid_preview_configuration_fails_closed(monkeypatch, origin):
    import app as module

    try:
        with monkeypatch.context() as environment:
            environment.setenv("CORS_ORIGINS", origin)
            with pytest.raises(ValueError, match="CORS_ORIGINS"):
                importlib.reload(module)
    finally:
        importlib.reload(module)
