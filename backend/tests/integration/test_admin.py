import base64
import json
import re

import httpx
from sqlalchemy import select, text

from app.core.passwords import verify_password
from app.models import Admin, AdminRole, AuditLog, RequestLog, Vendor
from app.repositories import admins as admin_repo
from app.services.admins import create_admin
from tests.integration.conftest import Api, MakeApi

PASSWORD = "correct horse battery staple"


async def make_admin(
    api: Api, email: str = "boss@example.com", role: AdminRole = AdminRole.SUPERADMIN
) -> Admin:
    async with api.app.state.session_factory() as db:
        admin = await create_admin(db, email=email, password=PASSWORD, role=role)
        await db.commit()
    return admin


def token_in(html: str) -> str:
    match = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert match, "no csrf token in page"
    return match.group(1)


async def login(
    api: Api, email: str = "boss@example.com", password: str = PASSWORD
) -> httpx.Response:
    page = await api.client.get("/admin/login")
    return await api.client.post(
        "/admin/login",
        data={"email": email, "password": password, "csrf_token": token_in(page.text)},
    )


async def logged_in(api: Api, role: AdminRole = AdminRole.SUPERADMIN) -> str:
    """Create an admin, log in, return the session's CSRF token."""
    await make_admin(api, role=role)
    response = await login(api)
    assert response.status_code == 303
    dashboard = await api.client.get("/admin")
    assert dashboard.status_code == 200
    return token_in(dashboard.text)


async def post(api: Api, path: str, csrf: str, **fields: str) -> httpx.Response:
    return await api.client.post(path, data={"csrf_token": csrf, **fields})


async def audit_actions(api: Api) -> list[str]:
    async with api.app.state.session_factory() as db:
        return list(await db.scalars(select(AuditLog.action).order_by(AuditLog.id)))


# --- access, login, sessions -----------------------------------------------------------------


async def test_pages_need_a_login(make_api: MakeApi) -> None:
    api = await make_api()
    for path in ("/admin", "/admin/vendors", "/admin/logs", "/admin/audit"):
        response = await api.client.get(path)
        assert response.status_code == 303, path
        assert response.headers["location"] == "/admin/login"
    # POSTs cannot be done without a session either
    response = await api.client.post("/admin/vendors/new", data={"business_name": "x"})
    assert response.status_code == 303


async def test_login_page_has_security_headers_and_no_scripts(make_api: MakeApi) -> None:
    api = await make_api()
    response = await api.client.get("/admin/login")
    assert response.status_code == 200
    csp = response.headers["content-security-policy"]
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "no-store"
    assert "<script" not in response.text
    assert "style=" not in response.text
    cookie = response.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie


async def test_login_works_and_logout_ends_the_session(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    assert (await api.client.get("/admin/vendors")).status_code == 200

    response = await post(api, "/admin/logout", csrf)
    assert response.status_code == 303
    api.client.cookies.clear()  # the browser would drop it; the server must not need that
    assert (await api.client.get("/admin")).status_code == 303


async def test_logout_invalidates_the_session_on_the_server(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    stolen = dict(api.client.cookies)
    await post(api, "/admin/logout", csrf)
    api.client.cookies.clear()
    for name, value in stolen.items():
        api.client.cookies.set(name, value, path="/admin")
    assert (await api.client.get("/admin")).status_code == 303


async def test_wrong_password_and_unknown_email_give_the_same_answer(make_api: MakeApi) -> None:
    api = await make_api()
    await make_admin(api)
    wrong_pw = await login(api, password="not the password")
    unknown = await login(api, email="nobody@example.com")
    assert wrong_pw.status_code == unknown.status_code == 401
    assert "Wrong email or password." in wrong_pw.text
    assert "Wrong email or password." in unknown.text


async def test_login_form_needs_its_csrf_cookie_and_token(make_api: MakeApi) -> None:
    api = await make_api()
    await make_admin(api)
    response = await api.client.post(
        "/admin/login", data={"email": "boss@example.com", "password": PASSWORD}
    )
    assert response.status_code == 403
    page = await api.client.get("/admin/login")
    api.client.cookies.clear()  # token in the form, but no cookie: a cross-site post
    response = await api.client.post(
        "/admin/login",
        data={"email": "boss@example.com", "password": PASSWORD, "csrf_token": token_in(page.text)},
    )
    assert response.status_code == 403


async def test_repeated_failures_lock_the_account_even_for_the_right_password(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    await make_admin(api)
    for _ in range(5):
        assert (await login(api, password="wrong wrong wrong")).status_code == 401
    locked = await login(api)  # correct password now
    assert locked.status_code == 429
    assert "Too many failed attempts" in locked.text


async def test_deactivated_admin_cannot_log_in_and_loses_the_session(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    async with api.app.state.session_factory() as db:
        admin = await admin_repo.get_by_email(db, "boss@example.com")
        assert admin is not None
        admin.is_active = False
        await db.commit()
    assert (await api.client.get("/admin")).status_code == 303
    assert (await login(api)).status_code == 401


async def test_passwords_are_stored_as_argon2id_hashes(make_api: MakeApi) -> None:
    api = await make_api()
    await make_admin(api)
    async with api.app.state.session_factory() as db:
        admin = await admin_repo.get_by_email(db, "boss@example.com")
        assert admin is not None
        assert admin.password_hash.startswith("$argon2id$")
        assert PASSWORD not in admin.password_hash
        assert verify_password(admin.password_hash, PASSWORD)


async def test_ip_allowlist_blocks_other_addresses(make_api: MakeApi) -> None:
    api = await make_api(admin_ip_allowlist="10.1.2.0/24")
    response = await api.client.get("/admin/login")
    assert response.status_code == 403
    allowed = await make_api(admin_ip_allowlist="10.1.2.0/24, 127.0.0.0/8")
    assert (await allowed.client.get("/admin/login")).status_code == 200


async def test_api_routes_still_answer_in_json(make_api: MakeApi) -> None:
    api = await make_api()
    response = await api.client.get("/v1/does-not-exist")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# --- vendors ---------------------------------------------------------------------------------

NEW_VENDOR = {
    "business_name": "Bridal House",
    "whatsapp_number": "923001230001",
    "email": "owner@bridal.example",
    "bot_base_url": "http://bot.test",
    "default_country_code": "92",
    "marketing_daily_cap": "200",
    "inbound_mode": "forward",
    "outbound_mode": "real_plugin",
}


async def test_creating_a_vendor_shows_the_secrets_once_and_audits_without_them(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    csrf = await logged_in(api)

    response = await post(api, "/admin/vendors/new", csrf, **NEW_VENDOR)

    assert response.status_code == 200
    assert "Vendor created" in response.text
    live = re.search(r"wab_live_[A-Za-z0-9_\-]+", response.text)
    assert live
    async with api.app.state.session_factory() as db:
        vendor = await db.scalar(select(Vendor).where(Vendor.business_name == "Bridal House"))
        assert vendor is not None
        audit_rows = [(a.action, a.target) for a in await db.scalars(select(AuditLog))]
    assert ("vendor.create", f"vendor:{vendor.id}") in audit_rows
    assert live.group(0) not in str(audit_rows)

    again = await api.client.get(f"/admin/vendors/{vendor.id}")
    assert again.status_code == 200
    assert live.group(0) not in again.text  # only the prefix is ever shown again


async def test_the_created_live_key_works_against_the_plugin_api(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    response = await post(api, "/admin/vendors/new", csrf, **NEW_VENDOR)
    live = re.search(r"wab_live_[A-Za-z0-9_\-]+", response.text)
    assert live
    api.numbers[live.group(0)] = NEW_VENDOR["whatsapp_number"]
    check = await api.post_reply(live.group(0))
    assert check.status_code == 200


async def test_create_vendor_form_errors_are_shown_and_keep_the_input(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    response = await post(
        api, "/admin/vendors/new", csrf, **{**NEW_VENDOR, "bot_base_url": "not a url"}
    )
    assert response.status_code == 400
    assert 'class="error"' in response.text
    assert "Bridal House" in response.text
    async with api.app.state.session_factory() as db:
        assert await db.scalar(select(Vendor.id)) is None


async def test_post_without_the_session_csrf_token_is_refused(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    response = await post(api, "/admin/vendors/new", "wrong-token", **NEW_VENDOR)
    assert response.status_code == 403
    assert "expired" in response.text
    async with api.app.state.session_factory() as db:
        assert await db.scalar(select(Vendor.id)) is None


async def test_support_role_can_look_and_test_but_not_change(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api, role=AdminRole.SUPPORT)

    assert (await api.client.get("/admin/vendors")).status_code == 200
    assert (await api.client.get(f"/admin/vendors/{created.vendor.id}")).status_code == 200
    assert (await api.client.get(f"/admin/vendors/{created.vendor.id}/lab")).status_code == 200
    assert (await api.client.get("/admin/vendors/new")).status_code == 403
    assert (await api.client.get("/admin/audit")).status_code == 403

    vid = created.vendor.id
    for path, fields in (
        ("/admin/vendors/new", NEW_VENDOR),
        (f"/admin/vendors/{vid}/status", {"action": "suspend", "confirm": "on"}),
        (f"/admin/vendors/{vid}/keys/rotate", {"kind": "live"}),
        (f"/admin/vendors/{vid}/modes", {"inbound_mode": "echo", "outbound_mode": "dry_run"}),
    ):
        response = await post(api, path, csrf, **fields)
        assert response.status_code == 403, path
    assert await audit_actions(api) == ["login"]


async def test_vendor_names_are_html_escaped(make_api: MakeApi) -> None:
    api = await make_api()
    await api.vendor(business_name="<script>alert(1)</script>")
    await logged_in(api)
    page = await api.client.get("/admin/vendors")
    assert "<script>alert(1)</script>" not in page.text
    assert "&lt;script&gt;" in page.text


async def test_unknown_vendor_gives_an_html_error_page(make_api: MakeApi) -> None:
    api = await make_api()
    await logged_in(api)
    response = await api.client.get("/admin/vendors/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404
    assert "text/html" in response.headers["content-type"]
    assert "no vendor with this id" in response.text


async def test_suspending_a_vendor_stops_its_keys_and_is_audited(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api)
    vid = created.vendor.id
    assert (await api.post_reply(created.secrets.live_key)).status_code == 200

    unconfirmed = await post(api, f"/admin/vendors/{vid}/status", csrf, action="suspend")
    assert unconfirmed.status_code == 400
    assert (await api.post_reply(created.secrets.live_key)).status_code == 200

    done = await post(api, f"/admin/vendors/{vid}/status", csrf, action="suspend", confirm="on")
    assert done.status_code == 303
    assert (await api.post_reply(created.secrets.live_key)).status_code in (401, 403)
    assert "vendor.status_change" in await audit_actions(api)

    back = await post(api, f"/admin/vendors/{vid}/status", csrf, action="activate")
    assert back.status_code == 303
    assert (await api.post_reply(created.secrets.live_key)).status_code == 200


async def test_mode_change_needs_confirmation_when_leaving_forward(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api)
    path = f"/admin/vendors/{created.vendor.id}/modes"

    refused = await post(api, path, csrf, inbound_mode="echo", outbound_mode="real_plugin")
    assert refused.status_code == 400
    assert "confirm" in refused.text

    ok = await post(api, path, csrf, inbound_mode="echo", outbound_mode="real_plugin", confirm="on")
    assert ok.status_code == 303
    page = await api.client.get(f"/admin/vendors/{created.vendor.id}")
    assert "Inbound mode is <b>echo</b>" in page.text
    async with api.app.state.session_factory() as db:
        row = await db.scalar(select(AuditLog).where(AuditLog.action == "vendor.mode_change"))
        assert row is not None
        assert "forward->echo" in (row.target or "")

    # going back to forward needs no confirmation
    back = await post(api, path, csrf, inbound_mode="forward", outbound_mode="real_plugin")
    assert back.status_code == 303


async def test_mock_bot_mode_is_refused_where_it_is_disabled(make_api: MakeApi) -> None:
    api = await make_api(enable_mock_bot=False)
    created = await api.vendor()
    csrf = await logged_in(api)
    response = await post(
        api,
        f"/admin/vendors/{created.vendor.id}/modes",
        csrf,
        inbound_mode="mock_bot",
        outbound_mode="real_plugin",
        confirm="on",
    )
    assert response.status_code == 400


async def test_rotating_and_revoking_keys(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api)
    vid = created.vendor.id

    rotated = await post(api, f"/admin/vendors/{vid}/keys/rotate", csrf, kind="live")
    assert rotated.status_code == 200
    new_key = re.search(r"wab_live_[A-Za-z0-9_\-]+", rotated.text)
    assert new_key
    api.numbers[new_key.group(0)] = created.vendor.phone_e164
    assert (await api.post_reply(created.secrets.live_key)).status_code in (401, 403)
    assert (await api.post_reply(new_key.group(0))).status_code == 200
    audit = [a for a in await audit_actions(api) if a.startswith("key.")]
    assert audit == ["key.rotate"]

    async with api.app.state.session_factory() as db:
        keys = await db.execute(
            text("select id from api_keys where status='active' and kind='live'")
        )
        (key_id,) = keys.one()
    revoked = await post(api, f"/admin/vendors/{vid}/keys/{key_id}/revoke", csrf)
    assert revoked.status_code == 303
    assert (await api.post_reply(new_key.group(0))).status_code in (401, 403)


async def test_canned_rules_can_be_added_and_deleted(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="canned")
    csrf = await logged_in(api)
    vid = created.vendor.id

    bad = await post(
        api, f"/admin/vendors/{vid}/rules", csrf,
        match_type="text_contains", match_value="", reply_text="hi",
    )  # fmt: skip
    assert bad.status_code == 400

    added = await post(
        api, f"/admin/vendors/{vid}/rules", csrf,
        match_type="text_contains", match_value="price", reply_text="85,000 se shuru",
    )  # fmt: skip
    assert added.status_code == 303
    page = await api.client.get(f"/admin/vendors/{vid}")
    assert "85,000 se shuru" in page.text

    reply = await api.post_reply(created.secrets.live_key, {
        "chatId": "923001112222@c.us", "chatTitle": "S", "senderName": "S",
        "senderNumber": "+923001112222", "isSaved": False,
        "incomingMessages": [{"text": "price kya hai", "timestamp": "10:00:00"}],
        "chatHistory": [],
    })  # fmt: skip
    assert reply.json()["replies"] == ["85,000 se shuru"]

    rule_id = re.search(r"/rules/([0-9a-f-]{36})/delete", page.text)
    assert rule_id
    deleted = await post(api, f"/admin/vendors/{vid}/rules/{rule_id.group(1)}/delete", csrf)
    assert deleted.status_code == 303
    assert "85,000 se shuru" not in (await api.client.get(f"/admin/vendors/{vid}")).text


# --- Test Lab --------------------------------------------------------------------------------


async def test_lab_simulation_runs_the_pipeline_and_logs_metadata_only(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="echo")
    csrf = await logged_in(api)
    vid = created.vendor.id

    response = await api.client.post(
        f"/admin/vendors/{vid}/lab/simulate",
        data={"csrf_token": csrf, "text": "secret customer words", "scenario": "ok"},
    )
    assert response.status_code == 200
    assert "PASS" in response.text
    assert "secret customer words" in response.text  # shown to the admin who typed it
    assert not api.bot.requests  # echo mode never calls the bot

    async with api.app.state.session_factory() as db:
        rows = list(await db.scalars(select(RequestLog)))
    assert [(r.kind, r.mode, r.status) for r in rows] == [("lab", "echo", "ok")]
    assert "secret customer words" not in repr([vars(r) for r in rows])


async def test_lab_can_call_the_real_bot_and_shows_the_exchange(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor(inbound_mode="echo")
    csrf = await logged_in(api)
    api.bot.handler = lambda _r: httpx.Response(200, json={"replies": ["from the bot"]})

    response = await api.client.post(
        f"/admin/vendors/{created.vendor.id}/lab/simulate",
        data={"csrf_token": csrf, "text": "hello", "use_real_bot": "on", "scenario": "ok"},
    )

    assert response.status_code == 200
    assert "from the bot" in response.text
    assert "X-Gateway-Signature" in response.text
    assert created.secrets.bot_secret not in response.text
    assert len(api.bot.requests) == 1


async def test_lab_file_upload_goes_to_the_bot_without_leaking_bytes_into_the_page(
    make_api: MakeApi,
) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api)

    response = await api.client.post(
        f"/admin/vendors/{created.vendor.id}/lab/simulate",
        data={"csrf_token": csrf, "text": "", "scenario": "ok"},
        files={"file": ("dress.jpg", b"\xff\xd8\xff jpeg bytes", "image/jpeg")},
    )

    assert response.status_code == 200
    sent = json.loads(api.bot.requests[0].content)
    encoded = base64.b64encode(b"\xff\xd8\xff jpeg bytes").decode()
    assert sent["incomingMessages"][0]["media"]["mediaType"] == "image"
    assert encoded in api.bot.requests[0].content.decode()
    assert encoded not in response.text


async def test_lab_rejects_an_empty_form_and_a_missing_csrf_token(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api)
    path = f"/admin/vendors/{created.vendor.id}/lab/simulate"
    empty = await post(api, path, csrf, text="", scenario="ok")
    assert empty.status_code == 400
    forged = await post(api, path, "nope", text="hi", scenario="ok")
    assert forged.status_code == 403
    assert not api.bot.requests


async def test_bot_connection_test_reports_pass_and_fail(make_api: MakeApi) -> None:
    api = await make_api()
    created = await api.vendor()
    csrf = await logged_in(api)
    path = f"/admin/vendors/{created.vendor.id}/lab/test-bot"

    ok = await post(api, path, csrf)
    assert ok.status_code == 200
    assert "PASS" in ok.text

    api.bot.handler = lambda _r: httpx.Response(500, text="boom")
    bad = await post(api, path, csrf)
    assert "FAIL" in bad.text

    async with api.app.state.session_factory() as db:
        kinds = [
            (r.kind, r.status) for r in await db.scalars(select(RequestLog).order_by(RequestLog.id))
        ]
    assert kinds == [("bot_test", "ok"), ("bot_test", "error")]


async def test_logs_page_filters_by_vendor(make_api: MakeApi) -> None:
    api = await make_api()
    one = await api.vendor(business_name="Alpha Shop")
    two = await api.vendor(business_name="Beta Shop")
    csrf = await logged_in(api)
    await post(api, f"/admin/vendors/{one.vendor.id}/lab/test-bot", csrf)
    await post(api, f"/admin/vendors/{two.vendor.id}/lab/test-bot", csrf)

    both = await api.client.get("/admin/logs")
    assert both.text.count("bot_test") == 2
    only = await api.client.get(f"/admin/logs?vendor_id={one.vendor.id}")
    assert only.text.count("bot_test") == 1
    bad = await api.client.get("/admin/logs?vendor_id=nonsense")
    assert bad.status_code == 400


async def test_audit_page_lists_actions_for_superadmins(make_api: MakeApi) -> None:
    api = await make_api()
    csrf = await logged_in(api)
    await post(api, "/admin/vendors/new", csrf, **NEW_VENDOR)
    page = await api.client.get("/admin/audit")
    assert page.status_code == 200
    assert "vendor.create" in page.text
    assert "boss@example.com" in page.text


async def test_dashboard_counts_vendors(make_api: MakeApi) -> None:
    api = await make_api()
    await api.vendor()
    await logged_in(api)
    page = await api.client.get("/admin")
    assert page.status_code == 200
    assert "vendors (1 active)" in page.text


async def test_static_css_is_served_without_a_login(make_api: MakeApi) -> None:
    api = await make_api()
    response = await api.client.get("/admin/static/admin.css")
    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
