import httpx
import pytest
from github_gate.github import GITHUB_API_BASE, STATUS_CONTEXT, set_commit_status


def test_posts_status_with_contract_context():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["Authorization"]
        seen["body"] = request.read().decode()
        return httpx.Response(201, json={})

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        set_commit_status(
            "acme/ml-repo",
            "abc123",
            "success",
            token="tok",
            description="ok",
            http=http,
        )

    assert seen["url"] == f"{GITHUB_API_BASE}/repos/acme/ml-repo/statuses/abc123"
    assert seen["auth"] == "Bearer tok"
    assert f'"context":"{STATUS_CONTEXT}"' in seen["body"].replace(" ", "")
    assert STATUS_CONTEXT == "deploy-gate/approval"


def test_raises_on_github_error():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(404, text="Not Found"))
    ) as http:
        with pytest.raises(RuntimeError, match="404"):
            set_commit_status("acme/ml-repo", "abc123", "success", token="t", http=http)
