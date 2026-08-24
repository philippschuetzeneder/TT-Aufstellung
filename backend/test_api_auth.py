import os

import pytest

from app import api_auth


def test_admin_not_required_when_token_unset(monkeypatch):
    monkeypatch.setattr(api_auth, "ADMIN_TOKEN", "")
    assert api_auth.admin_required() is False
    assert api_auth.token_is_valid("") is True
    assert api_auth.token_is_valid("anything") is True


def test_token_is_valid_when_token_set(monkeypatch):
    monkeypatch.setattr(api_auth, "ADMIN_TOKEN", "secret-token")
    assert api_auth.admin_required() is True
    assert api_auth.token_is_valid("secret-token") is True
    assert api_auth.token_is_valid("wrong") is False
    assert api_auth.token_is_valid("") is False


def test_path_requires_admin_classification(monkeypatch):
    monkeypatch.setattr(api_auth, "ADMIN_TOKEN", "x")
    assert api_auth.path_requires_admin("/api/analysis") is True
    assert api_auth.path_requires_admin("/api/data/refresh") is True
    assert api_auth.path_requires_admin("/api/leagues") is False
    assert api_auth.path_requires_admin("/api/teams/players") is False


def test_extract_admin_token_from_headers():
    class Headers:
        def get(self, key, default=None):
            data = {
                "Authorization": "Bearer my-token",
                "X-Admin-Token": "ignored-when-bearer",
            }
            return data.get(key, default)

    assert api_auth.extract_admin_token(Headers()) == "my-token"

    class XHeaders:
        def get(self, key, default=None):
            data = {"X-Admin-Token": "header-token"}
            return data.get(key, default)

    assert api_auth.extract_admin_token(XHeaders()) == "header-token"
