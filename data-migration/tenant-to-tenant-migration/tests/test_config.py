import pytest

from kit.config import ConfigError, email_domains, load_tenant_config


@pytest.fixture
def env(monkeypatch):
    # setenv then delenv makes monkeypatch record each variable, so teardown also removes values
    # that load_dotenv adds during a test
    for name in ("TARGET_BASE_URL", "TARGET_TENANT", "TARGET_API_KEY", "VERIFY_SSL"):
        monkeypatch.setenv(name, "placeholder")
        monkeypatch.delenv(name)
    return monkeypatch


def test_reads_prefixed_settings(env, tmp_path):
    env.setenv("TARGET_BASE_URL", "https://api.example.com/")
    env.setenv("TARGET_TENANT", "tenant-b")
    env.setenv("TARGET_API_KEY", "key-b")
    cfg = load_tenant_config("TARGET", env_file=tmp_path / "missing.env")
    assert cfg.base_url == "https://api.example.com"
    assert cfg.tenant == "tenant-b"
    assert cfg.api_key == "key-b"  # pragma: allowlist secret
    assert cfg.verify_ssl is True


def test_missing_settings_are_all_named(env, tmp_path):
    env.setenv("TARGET_TENANT", "tenant-b")
    with pytest.raises(ConfigError) as exc:
        load_tenant_config("TARGET", env_file=tmp_path / "missing.env")
    assert "TARGET_BASE_URL" in str(exc.value)
    assert "TARGET_API_KEY" in str(exc.value)


def test_shell_environment_wins_over_env_file(env, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("TARGET_BASE_URL=https://file\nTARGET_TENANT=file\nTARGET_API_KEY=file\n")
    env.setenv("TARGET_TENANT", "shell")
    assert load_tenant_config("TARGET", env_file=env_file).tenant == "shell"


def test_verify_ssl_false(env, tmp_path):
    env.setenv("TARGET_BASE_URL", "https://x")
    env.setenv("TARGET_TENANT", "t")
    env.setenv("TARGET_API_KEY", "k")
    env.setenv("VERIFY_SSL", "false")
    assert load_tenant_config("TARGET", env_file=tmp_path / "missing.env").verify_ssl is False


def test_email_domains_parses_a_comma_list(monkeypatch):
    monkeypatch.setenv("USER_EMAIL_DOMAINS", " Credo.AI, @example.com ,,")
    assert email_domains("USER_EMAIL_DOMAINS") == {"credo.ai", "example.com"}


def test_email_domains_unset_is_empty(monkeypatch):
    monkeypatch.delenv("USER_EMAIL_DOMAINS", raising=False)
    assert email_domains("USER_EMAIL_DOMAINS") == set()
