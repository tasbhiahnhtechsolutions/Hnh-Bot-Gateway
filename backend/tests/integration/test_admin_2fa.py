"""Two-factor login for the admin panel (plan section 9.3)."""

import re
from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy import select

from app.core import totp
from app.models import Admin
from tests.integration.conftest import Api, MakeApi
from tests.integration.test_admin import (
    PASSWORD,
    audit_actions,
    logged_in,
    login,
    make_admin,
    post,
    token_in,
)

STEP = 60_000_000  # a fixed "now" (in 30 s steps) so a test cannot straddle a code change


@pytest.fixture(autouse=True)
def frozen_step(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(totp, "current_step", lambda now=None: STEP)
    yield


def code(secret: str, offset: int = 0) -> str:
    return totp.code_for_step(secret, STEP + offset)


async def enroll(api: Api, csrf: str) -> str:
    """Turn 2FA on for the logged-in admin. Returns the secret (uses the code of this step)."""
    assert (await post(api, "/admin/account/2fa/start", csrf)).status_code == 303
    page = await api.client.get("/admin/account")
    match = re.search(r'Key: <span class="mono">([A-Z2-7]+)</span>', page.text)
    assert match, "the account page shows no key"
    secret = match.group(1)
    response = await post(api, "/admin/account/2fa/enable", csrf, code=code(secret))
    assert response.status_code == 303
    return secret


async def password_step(api: Api) -> httpx.Response:
    api.client.cookies.clear()
    return await login(api)


async def second_step(api: Api, pending_page: str, one_time_code: str) -> httpx.Response:
    return await api.client.post(
        "/admin/login/2fa",
        data={
            "csrf_token": token_in(pending_page),
            "pending": re.search(r'name="pending" value="([^"]+)"', pending_page).group(1),  # type: ignore[union-attr]
            "code": one_time_code,
        },
    )


async def test_enrolling_turns_two_factor_on_and_stores_the_secret_encrypted(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    async with api.app.state.session_factory() as db:
        stored = await db.scalar(select(Admin.totp_secret_enc))
    assert stored and secret not in stored
    assert api.box.decrypt(stored) == secret
    assert "admin.2fa_enable" in await audit_actions(api)
    page = await api.client.get("/admin/account")
    assert ">on</span>" in page.text
    assert secret not in page.text  # shown once, during setup


async def test_a_wrong_code_does_not_turn_it_on(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    await post(api, "/admin/account/2fa/start", csrf)
    page = await api.client.get("/admin/account")
    secret = re.search(r'Key: <span class="mono">([A-Z2-7]+)</span>', page.text).group(1)  # type: ignore[union-attr]
    wrong = code(secret, 5)
    assert (await post(api, "/admin/account/2fa/enable", csrf, code=wrong)).status_code == 400
    async with api.app.state.session_factory() as db:
        assert await db.scalar(select(Admin.totp_secret_enc)) is None


async def test_enable_without_starting_is_refused(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    response = await post(api, "/admin/account/2fa/enable", csrf, code="123456")
    assert response.status_code == 400


async def test_login_needs_the_password_and_then_the_code(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)

    step_one = await password_step(api)
    assert step_one.status_code == 200
    assert "Second step" in step_one.text
    assert "gw_admin_session" not in step_one.headers.get("set-cookie", "")
    assert (await api.client.get("/admin")).status_code == 303  # still not logged in

    done = await second_step(api, step_one.text, code(secret, 1))
    assert done.status_code == 303
    assert (await api.client.get("/admin")).status_code == 200


async def test_a_wrong_code_is_refused_and_the_form_stays(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    step_one = await password_step(api)
    wrong = await second_step(api, step_one.text, code(secret, 7))
    assert wrong.status_code == 401
    assert "Wrong code" in wrong.text
    assert (await api.client.get("/admin")).status_code == 303
    # the same pending login still accepts the right code
    done = await second_step(api, wrong.text, code(secret, 1))
    assert done.status_code == 303


async def test_a_code_works_only_once(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    first = await password_step(api)
    assert (await second_step(api, first.text, code(secret, 1))).status_code == 303

    second = await password_step(api)
    replay = await second_step(api, second.text, code(secret, 1))
    assert replay.status_code == 401  # somebody watching over the shoulder cannot reuse it
    fresh = await second_step(api, replay.text, code(secret, -1))
    assert fresh.status_code == 303


async def test_the_code_used_to_enroll_cannot_log_in_right_away(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    step_one = await password_step(api)
    assert (await second_step(api, step_one.text, code(secret, 0))).status_code == 401


async def test_five_wrong_codes_end_the_pending_login(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    page = (await password_step(api)).text
    last: httpx.Response | None = None
    for attempt in range(5):
        last = await second_step(api, page, code(secret, 10 + attempt))
        assert last.status_code == 401
    assert last is not None
    assert "Start again" in last.text
    # even the right code does not help any more
    pending = re.search(r'name="pending" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
    dead = await api.client.post(
        "/admin/login/2fa",
        data={"csrf_token": token_in(last.text), "pending": pending, "code": code(secret, 1)},
    )
    assert dead.status_code == 401
    assert "Start again" in dead.text or "expired" in dead.text


async def test_the_second_step_needs_its_own_valid_pending_token(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    page = (await password_step(api)).text
    forged = await api.client.post(
        "/admin/login/2fa",
        data={"csrf_token": token_in(page), "pending": "not-a-real-token", "code": code(secret, 1)},
    )
    assert forged.status_code == 401
    no_csrf = await api.client.post(
        "/admin/login/2fa", data={"csrf_token": "x", "pending": "x", "code": code(secret, 1)}
    )
    assert no_csrf.status_code == 403
    assert (await api.client.get("/admin")).status_code == 303


async def test_an_admin_without_two_factor_still_logs_in_with_the_password(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    await make_admin(api)
    response = await login(api)
    assert response.status_code == 303


async def test_turning_it_off_needs_a_current_code(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    bad = await post(api, "/admin/account/2fa/disable", csrf, code=code(secret, 9))
    assert bad.status_code == 400
    async with api.app.state.session_factory() as db:
        assert await db.scalar(select(Admin.totp_secret_enc)) is not None
    ok = await post(api, "/admin/account/2fa/disable", csrf, code=code(secret, 1))
    assert ok.status_code == 303
    async with api.app.state.session_factory() as db:
        assert await db.scalar(select(Admin.totp_secret_enc)) is None
    assert "admin.2fa_disable" in await audit_actions(api)
    api.client.cookies.clear()
    assert (await login(api)).status_code == 303  # password alone again


async def test_the_account_forms_need_the_csrf_token(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    for path in ("start", "enable", "disable"):
        response = await api.client.post(f"/admin/account/2fa/{path}", data={"code": "123456"})
        assert response.status_code == 403, path


async def test_a_deactivated_admin_cannot_finish_a_pending_login(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    secret = await enroll(api, csrf)
    page = (await password_step(api)).text
    async with api.app.state.session_factory() as db:
        admin = await db.scalar(select(Admin))
        assert admin is not None
        admin.is_active = False
        await db.commit()
    response = await second_step(api, page, code(secret, 1))
    assert response.status_code == 401
    assert (await api.client.get("/admin")).status_code == 303


async def test_when_required_an_admin_without_it_can_only_open_the_account_page(
    make_api: MakeApi,
) -> None:
    api = await make_api(admin_require_2fa=True)
    await make_admin(api)
    assert (await login(api)).status_code == 303

    for path in ("/admin", "/admin/vendors", "/admin/logs"):
        response = await api.client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/admin/account")
    account = await api.client.get("/admin/account")
    assert account.status_code == 200
    csrf = token_in(account.text)

    secret = await enroll(api, csrf)
    assert secret
    assert (await api.client.get("/admin")).status_code == 200


async def test_the_password_is_checked_before_any_code_is_asked(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    await enroll(api, csrf)
    api.client.cookies.clear()
    page = await api.client.get("/admin/login")
    wrong = await api.client.post(
        "/admin/login",
        data={
            "email": "boss@example.com",
            "password": PASSWORD + "x",
            "csrf_token": token_in(page.text),
        },
    )
    assert wrong.status_code == 401
    assert "Second step" not in wrong.text
