from unittest.mock import MagicMock, patch

from mailbrain.gmail import auth


def test_scope_is_modify_not_delete():
    # Design v0.2: gmail.modify covers read+label+archive; never a delete scope.
    assert auth.SCOPES == ["https://www.googleapis.com/auth/gmail.modify"]


def test_valid_existing_token_skips_flow(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("{}")
    creds = MagicMock()
    creds.valid = True

    with (
        patch.object(
            auth.Credentials, "from_authorized_user_file", return_value=creds
        ) as from_file,
        patch.object(auth.InstalledAppFlow, "from_client_secrets_file") as flow,
    ):
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    from_file.assert_called_once()
    flow.assert_not_called()
    assert result is creds


def test_expired_token_refreshes(tmp_path):
    token = tmp_path / "token.json"
    token.write_text("{}")
    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = "r"
    creds.to_json.return_value = "{}"

    with (
        patch.object(auth.Credentials, "from_authorized_user_file", return_value=creds),
        patch.object(auth, "Request") as request,
    ):
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    creds.refresh.assert_called_once_with(request.return_value)
    assert result is creds


def test_no_token_runs_flow_and_persists(tmp_path):
    token = tmp_path / "token.json"  # does not exist
    new_creds = MagicMock()
    new_creds.to_json.return_value = '{"token": "x"}'
    flow_instance = MagicMock()
    flow_instance.run_local_server.return_value = new_creds

    with patch.object(
        auth.InstalledAppFlow, "from_client_secrets_file", return_value=flow_instance
    ) as from_secrets:
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    from_secrets.assert_called_once()
    flow_instance.run_local_server.assert_called_once()
    assert result is new_creds
    assert token.read_text() == '{"token": "x"}'


def test_build_service_uses_gmail_v1():
    creds = MagicMock()
    with patch.object(auth, "build") as build_mock:
        auth.build_service(creds)
    build_mock.assert_called_once_with("gmail", "v1", credentials=creds)


def test_stale_token_without_refresh_token_runs_fresh_flow(tmp_path):
    # Token exists but is invalid AND has no refresh_token -> must fall through
    # to a fresh InstalledAppFlow, not attempt a refresh.
    token = tmp_path / "token.json"
    token.write_text("{}")
    stale = MagicMock()
    stale.valid = False
    stale.expired = True
    stale.refresh_token = None

    new_creds = MagicMock()
    new_creds.to_json.return_value = '{"token": "fresh"}'
    flow_instance = MagicMock()
    flow_instance.run_local_server.return_value = new_creds

    with (
        patch.object(auth.Credentials, "from_authorized_user_file", return_value=stale),
        patch.object(
            auth.InstalledAppFlow,
            "from_client_secrets_file",
            return_value=flow_instance,
        ) as from_secrets,
    ):
        result = auth.load_credentials(tmp_path / "credentials.json", token)

    stale.refresh.assert_not_called()
    from_secrets.assert_called_once()
    assert result is new_creds
    assert token.read_text() == '{"token": "fresh"}'


def test_refresh_persists_token(tmp_path):
    # After a refresh, the refreshed credential must be written back to token_path.
    token = tmp_path / "token.json"
    token.write_text("{}")
    creds = MagicMock()
    creds.valid = False
    creds.expired = True
    creds.refresh_token = "r"
    creds.to_json.return_value = '{"token": "refreshed"}'

    with (
        patch.object(auth.Credentials, "from_authorized_user_file", return_value=creds),
        patch.object(auth, "Request"),
    ):
        auth.load_credentials(tmp_path / "credentials.json", token)

    assert token.read_text() == '{"token": "refreshed"}'
