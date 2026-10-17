"""Sign-in, account, logout and session endpoints under ``/api/auth`` (WP-4.1).

These routes are public in the authentication middleware; each one
authenticates its own input. They live under ``/api`` so the existing
development proxy and production ingress forward them unchanged.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
import hmac
import html
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.config.product import product_name
from app.observability.metrics import record_auth_rejection
from app.security.rate_limit import rate_limited

from .oidc import safe_redirect_path
from .service import (
    CSRF_COOKIE,
    CSRF_HEADER,
    DESKTOP_HANDOFF_TTL_SECONDS,
    MAX_PASSWORD_LENGTH,
    SECURE_SESSION_COOKIE,
    SESSION_COOKIE,
    AccountError,
    AuthenticatedPrincipal,
    AuthService,
    IssuedSession,
    ReauthenticationRequired,
)
from .settings import AuthMode

OIDC_TX_COOKIE = "omnix_oidc_tx"
_OIDC_TX_PATH = "/api/auth/oidc"
GOOGLE_TX_COOKIE = "omnix_google_tx"
# The system browser's binding for a desktop Google sign-in; its own name so it never meets the browser's own flow.
GOOGLE_DESKTOP_TX_COOKIE = "omnix_google_desktop_tx"
_GOOGLE_TX_PATH = "/api/auth/google"


class AuthOptionsResponse(BaseModel):
    """How someone may sign in here; the sign-in page shows only these."""

    registration: Literal["open", "invite", "closed"] = "closed"
    guests: bool = False
    google: bool = False
    # Google through the system browser, for the desktop app's windows (MarketPulse S-5).
    google_desktop: bool = False
    install_credential: bool = False
    min_password_length: int = 12


class AccountResponse(BaseModel):
    display_name: str
    email: str | None = None
    kind: Literal["standard", "guest"]
    has_password: bool
    google_linked: bool
    is_owner: bool


class AuthSessionResponse(BaseModel):
    enforced: bool
    mode: Literal["local", "oidc", "disabled"]
    authenticated: bool
    user_id: str | None = None
    workspace_id: str | None = None
    roles: list[str] = Field(default_factory=list)
    auth_method: str | None = None
    options: AuthOptionsResponse = Field(default_factory=AuthOptionsResponse)
    account: AccountResponse | None = None


class LocalLoginRequest(BaseModel):
    credential: str = Field(min_length=1, max_length=512)
    remember: bool = False


class PasswordLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    remember: bool = True


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    display_name: str | None = Field(default=None, max_length=200)
    invite: str | None = Field(default=None, max_length=256)
    remember: bool = True


class GuestUpgradeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)
    display_name: str | None = Field(default=None, max_length=200)
    remember: bool = True


class PasswordChangeRequest(BaseModel):
    # The current password; for a first password, the install credential or nothing after a recent sign-in.
    current: str | None = Field(default=None, max_length=MAX_PASSWORD_LENGTH)
    new_password: str = Field(min_length=1, max_length=MAX_PASSWORD_LENGTH)


class PasswordChangeResponse(BaseModel):
    signed_out_sessions: int


class EmailChangeRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    display_name: str | None = Field(default=None, max_length=200)


class InviteRequest(BaseModel):
    note: str | None = Field(default=None, max_length=200)


class InviteResponse(BaseModel):
    invite: str
    path: str
    expires_at: datetime


class InviteSummaryResponse(BaseModel):
    id: str
    note: str | None = None
    created_at: datetime
    expires_at: datetime


class InviteListResponse(BaseModel):
    invites: list[InviteSummaryResponse]


class InviteCheckResponse(BaseModel):
    usable: bool


class RedirectUrlResponse(BaseModel):
    url: str


class DesktopGoogleStartRequest(BaseModel):
    next: str = Field(default="/", max_length=2000)
    invite: str | None = Field(default=None, max_length=200)
    remember: bool = True
    # Connect Google to the signed-in account instead of signing in.
    link: bool = False


class DesktopGoogleStartResponse(BaseModel):
    """The window keeps ``nonce``; the desktop app opens ``begin_url`` in the system browser."""

    nonce: str
    begin_url: str
    expires_in: int


class DesktopGoogleHandoffRequest(BaseModel):
    nonce: str = Field(min_length=1, max_length=200)


class DesktopGoogleHandoffResponse(BaseModel):
    status: Literal["pending", "signed_in", "linked"]
    next: str


class AuthSessionSummary(BaseModel):
    id: str
    auth_method: str
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    current: bool


class AuthSessionListResponse(BaseModel):
    sessions: list[AuthSessionSummary]


class RevokeSessionsRequest(BaseModel):
    # One session's id from the list, or none for every session but this one.
    session_id: str | None = Field(default=None, min_length=16, max_length=16, pattern="^[0-9a-f]{16}$")
    # Local mode: the install credential again. OIDC mode: a recent sign-in instead.
    credential: str | None = Field(default=None, max_length=512)


class RevokeSessionsResponse(BaseModel):
    revoked: int


def _https(request: Request) -> bool:
    forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip().lower()
    return request.url.scheme == "https" or forwarded == "https"


def _secure(service: AuthService, request: Request) -> bool:
    """Cookies are Secure when configured so or when the browser came over HTTPS (ASVS 3.4.1)."""
    return service.settings.cookie_secure or _https(request)


def presented_session(request: Request) -> str | None:
    return request.cookies.get(SECURE_SESSION_COOKIE) or request.cookies.get(SESSION_COOKIE)


def _desktop_google_page(error: str | None) -> HTMLResponse:
    """What the system browser shows when a desktop Google sign-in ends there."""
    product = html.escape(product_name())
    if error is None:
        title, message = "Signed in", f"You are signed in. Return to {product}; you can close this tab."
    else:
        title = "Sign-in did not finish"
        message = f"Google sign-in did not finish ({html.escape(error)}). Return to {product} and try again."
    body = (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title>'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        "<style>body{font:16px system-ui,sans-serif;background:#131722;color:#d1d4dc;display:grid;place-items:center;"
        "min-height:100vh;margin:0}main{max-width:28rem;padding:2rem;text-align:center}</style></head>"
        f"<body><main><h1>{title}</h1><p>{message}</p></main></body></html>"
    )
    return HTMLResponse(body, status_code=200 if error is None else 400,
                        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


def _set_session_cookies(response: Response, issued: IssuedSession, *, secure: bool) -> None:
    response.set_cookie(
        SECURE_SESSION_COOKIE if secure else SESSION_COOKIE,
        issued.token,
        max_age=issued.max_age_seconds,
        httponly=True,
        samesite="strict",
        secure=secure,
        path="/",
    )
    # Readable by the web app, which echoes it in X-Omnix-CSRF.
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        max_age=issued.max_age_seconds,
        httponly=False,
        samesite="strict",
        secure=secure,
        path="/",
    )


def _clear_session_cookies(response: Response, *, secure: bool) -> None:
    for name, http_only in ((SESSION_COOKIE, True), (CSRF_COOKIE, False)):
        response.delete_cookie(name, path="/", secure=secure, httponly=http_only, samesite="strict")
    if secure:
        response.delete_cookie(SECURE_SESSION_COOKIE, path="/", secure=True, httponly=True, samesite="strict")


def _user_agent(request: Request) -> str | None:
    value = request.headers.get("user-agent")
    return value[:512] if value else None


def create_auth_router(get_service: Callable[[], AuthService | None]) -> APIRouter:
    router = APIRouter(prefix="/api/auth", tags=["auth"])
    # Sign-in attempts are limited per client address (WP-4.10).
    login_rate_limit = rate_limited("login")

    def service_for(mode: AuthMode | None = None) -> AuthService:
        service = get_service()
        if service is None or not service.settings.enforced:
            raise HTTPException(status_code=404, detail="authentication_not_enforced")
        if mode is not None and service.settings.mode is not mode:
            raise HTTPException(status_code=404, detail="auth_mode_inactive")
        return service

    def revoke_presented(service: AuthService, request: Request) -> None:
        # Rotation: a new login always replaces any session the browser held.
        previous = presented_session(request)
        if previous:
            service.logout(previous)

    def options_for(service: AuthService) -> AuthOptionsResponse:
        if service.settings.mode is not AuthMode.LOCAL:
            return AuthOptionsResponse()
        policy = service.settings.accounts
        return AuthOptionsResponse(
            registration=policy.registration,
            guests=policy.guests,
            google=policy.google is not None,
            google_desktop=policy.google is not None and policy.desktop_handoff,
            install_credential=True,
        )

    def session_payload(service: AuthService, principal: AuthenticatedPrincipal) -> AuthSessionResponse:
        account = service.account_view(principal) if service.settings.mode is AuthMode.LOCAL else None
        return AuthSessionResponse(
            enforced=True,
            mode=service.settings.mode.value,
            authenticated=True,
            user_id=principal.user_id,
            workspace_id=principal.context.workspace_id,
            roles=sorted(principal.context.roles),
            auth_method=principal.auth_method,
            options=options_for(service),
            account=AccountResponse(
                display_name=account.display_name, email=account.email,
                kind="guest" if account.kind == "guest" else "standard",
                has_password=account.has_password, google_linked=account.google_linked, is_owner=account.is_owner,
            ) if account is not None else None,
        )

    def signed_in_response(service: AuthService, issued: IssuedSession, request: Request,
                           response: Response) -> AuthSessionResponse:
        revoke_presented(service, request)
        _set_session_cookies(response, issued, secure=_secure(service, request))
        return session_payload(service, issued.principal)

    def account_failure(exc: AccountError) -> HTTPException:
        record_auth_rejection(exc.code)
        return HTTPException(status_code=exc.status, detail=exc.code)

    @router.get("/session", response_model=AuthSessionResponse)
    def current_session(request: Request) -> AuthSessionResponse:
        service = get_service()
        if service is None or not service.settings.enforced:
            mode = service.settings.mode if service is not None else AuthMode.DISABLED
            return AuthSessionResponse(enforced=False, mode=mode.value, authenticated=False)
        token = presented_session(request)
        principal = service.authenticate_session(token) if token else None
        if principal is None:
            # 200, not 401: the sign-in page reads the mode and options from this response.
            return AuthSessionResponse(enforced=True, mode=service.settings.mode.value, authenticated=False,
                                       options=options_for(service))
        return session_payload(service, principal)

    @router.post("/local/login", response_model=AuthSessionResponse, dependencies=[Depends(login_rate_limit)])
    def local_login(body: LocalLoginRequest, request: Request, response: Response) -> AuthSessionResponse:
        service = service_for(AuthMode.LOCAL)
        issued = service.login_with_install_credential(
            body.credential, user_agent=_user_agent(request), remember=body.remember
        )
        if issued is None:
            record_auth_rejection("invalid_credential")
            raise HTTPException(status_code=401, detail="invalid_credential")
        return signed_in_response(service, issued, request, response)

    @router.post("/password/login", response_model=AuthSessionResponse, dependencies=[Depends(login_rate_limit)])
    def password_login(body: PasswordLoginRequest, request: Request, response: Response) -> AuthSessionResponse:
        service = service_for(AuthMode.LOCAL)
        issued = service.login_with_password(email=body.email, password=body.password, remember=body.remember,
                                             user_agent=_user_agent(request))
        if issued is None:
            record_auth_rejection("invalid_credentials")
            # One answer for an unknown email and a wrong password.
            raise HTTPException(status_code=401, detail="invalid_credentials")
        return signed_in_response(service, issued, request, response)

    @router.post("/register", response_model=AuthSessionResponse, status_code=201,
                 dependencies=[Depends(login_rate_limit)])
    def register(body: RegisterRequest, request: Request, response: Response) -> AuthSessionResponse:
        service = service_for(AuthMode.LOCAL)
        try:
            issued = service.register(email=body.email, password=body.password, display_name=body.display_name,
                                      invite=body.invite, remember=body.remember, user_agent=_user_agent(request))
        except AccountError as exc:
            raise account_failure(exc) from exc
        return signed_in_response(service, issued, request, response)

    @router.post("/guest", response_model=AuthSessionResponse, status_code=201,
                 dependencies=[Depends(login_rate_limit)])
    def guest(request: Request, response: Response) -> AuthSessionResponse:
        service = service_for(AuthMode.LOCAL)
        try:
            issued = service.login_as_guest(user_agent=_user_agent(request))
        except AccountError as exc:
            raise account_failure(exc) from exc
        return signed_in_response(service, issued, request, response)

    @router.get("/invites/check", response_model=InviteCheckResponse)
    def check_invite(invite: str = "") -> InviteCheckResponse:
        service = service_for(AuthMode.LOCAL)
        return InviteCheckResponse(usable=service.invite_status(invite))

    @router.get("/local/callback", response_class=RedirectResponse, status_code=303, dependencies=[Depends(login_rate_limit)])
    def local_callback(request: Request, code: str = "", next: str = "/") -> RedirectResponse:
        service = service_for(AuthMode.LOCAL)
        issued = service.exchange_login_code(code, user_agent=_user_agent(request))
        if issued is None:
            return RedirectResponse("/login?error=login_link_expired", status_code=303)
        revoke_presented(service, request)
        redirect = RedirectResponse(safe_redirect_path(next), status_code=303)
        _set_session_cookies(redirect, issued, secure=_secure(service, request))
        return redirect

    def signed_in(service: AuthService, request: Request, *, check_csrf: bool) -> AuthenticatedPrincipal:
        token = presented_session(request)
        principal = service.authenticate_session(token) if token else None
        if principal is None:
            raise HTTPException(status_code=401, detail="authentication_required")
        if check_csrf:
            supplied = request.headers.getlist(CSRF_HEADER)
            if len(supplied) != 1 or not principal.csrf_token or not hmac.compare_digest(supplied[0], principal.csrf_token):
                raise HTTPException(status_code=403, detail="csrf_failed")
        return principal

    @router.post("/guest/upgrade", response_model=AuthSessionResponse, dependencies=[Depends(login_rate_limit)])
    def upgrade_guest(body: GuestUpgradeRequest, request: Request, response: Response) -> AuthSessionResponse:
        service = service_for(AuthMode.LOCAL)
        principal = signed_in(service, request, check_csrf=True)
        try:
            issued = service.upgrade_guest(principal, email=body.email, password=body.password,
                                           display_name=body.display_name, remember=body.remember,
                                           user_agent=_user_agent(request))
        except AccountError as exc:
            raise account_failure(exc) from exc
        _set_session_cookies(response, issued, secure=_secure(service, request))
        return session_payload(service, issued.principal)

    @router.post("/password", response_model=PasswordChangeResponse, dependencies=[Depends(login_rate_limit)])
    def change_password(body: PasswordChangeRequest, request: Request) -> PasswordChangeResponse:
        """Set or change the caller's password; their other sessions are signed out."""
        service = service_for(AuthMode.LOCAL)
        principal = signed_in(service, request, check_csrf=True)
        try:
            revoked = service.change_password(principal, current=body.current, new_password=body.new_password)
        except AccountError as exc:
            raise account_failure(exc) from exc
        except ReauthenticationRequired as exc:
            record_auth_rejection("reauthentication_required")
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return PasswordChangeResponse(signed_out_sessions=revoked)

    @router.post("/email", status_code=204, dependencies=[Depends(login_rate_limit)])
    def change_email(body: EmailChangeRequest, request: Request) -> Response:
        service = service_for(AuthMode.LOCAL)
        principal = signed_in(service, request, check_csrf=True)
        try:
            service.set_email(principal, email=body.email, display_name=body.display_name)
        except AccountError as exc:
            raise account_failure(exc) from exc
        return Response(status_code=204)

    def admin(service: AuthService, request: Request, *, check_csrf: bool) -> AuthenticatedPrincipal:
        from app.security.permissions import has_permission

        principal = signed_in(service, request, check_csrf=check_csrf)
        if not has_permission(principal.context.roles, "admin:users"):
            raise HTTPException(status_code=403, detail="permission_denied:admin:users")
        return principal

    @router.post("/invites", response_model=InviteResponse, status_code=201)
    def create_invite(body: InviteRequest, request: Request) -> InviteResponse:
        """A single-use link that lets one person register, for seven days."""
        service = service_for(AuthMode.LOCAL)
        principal = admin(service, request, check_csrf=True)
        issued = service.create_invite(principal, note=body.note)
        return InviteResponse(invite=issued.token, path=f"/login?invite={issued.token}", expires_at=issued.expires_at)

    @router.get("/invites", response_model=InviteListResponse)
    def list_invites(request: Request) -> InviteListResponse:
        service = service_for(AuthMode.LOCAL)
        principal = admin(service, request, check_csrf=False)
        return InviteListResponse(invites=[
            InviteSummaryResponse(id=item.handle, note=item.note, created_at=item.created_at, expires_at=item.expires_at)
            for item in service.list_invites(principal)
        ])

    @router.post("/invites/{invite_id}/revoke", status_code=204)
    def revoke_invite(invite_id: str, request: Request) -> Response:
        service = service_for(AuthMode.LOCAL)
        principal = admin(service, request, check_csrf=True)
        if not service.revoke_invite(principal, invite_id[:16]):
            raise HTTPException(status_code=404, detail="invite_not_found")
        return Response(status_code=204)

    def google_redirect(service: AuthService, request: Request, url: str, binding: str) -> RedirectResponse:
        redirect = RedirectResponse(url, status_code=302)
        # Lax: Google returns the browser with a cross-site top-level navigation.
        redirect.set_cookie(GOOGLE_TX_COOKIE, binding, max_age=600, httponly=True, samesite="lax",
                            secure=_secure(service, request), path=_GOOGLE_TX_PATH)
        return redirect

    @router.get("/google/login", response_class=RedirectResponse, status_code=302)
    def google_login(request: Request, next: str = "/", invite: str = "", remember: bool = True) -> RedirectResponse:
        service = service_for(AuthMode.LOCAL)
        try:
            url, binding = service.start_google_login(redirect_after=next, invite=invite or None, remember=remember)
        except AccountError:
            return RedirectResponse("/login?error=google_not_configured", status_code=303)
        return google_redirect(service, request, url, binding)

    @router.post("/google/link", response_model=RedirectUrlResponse)
    def google_link(request: Request, response: Response, next: str = "/") -> RedirectUrlResponse:
        """Start connecting Google to the signed-in account; the browser then opens ``url``."""
        service = service_for(AuthMode.LOCAL)
        principal = signed_in(service, request, check_csrf=True)
        try:
            url, binding = service.start_google_login(redirect_after=next, link_user=principal.user_id)
        except AccountError as exc:
            raise account_failure(exc) from exc
        response.set_cookie(GOOGLE_TX_COOKIE, binding, max_age=600, httponly=True, samesite="lax",
                            secure=_secure(service, request), path=_GOOGLE_TX_PATH)
        return RedirectUrlResponse(url=url)

    @router.get("/google/callback", response_class=RedirectResponse, status_code=303,
                dependencies=[Depends(login_rate_limit)])
    def google_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
        service = service_for(AuthMode.LOCAL)
        bindings = [request.cookies.get(GOOGLE_TX_COOKIE) or "", request.cookies.get(GOOGLE_DESKTOP_TX_COOKIE) or ""]
        issued, target = service.complete_google_login(code=code, state=state, browser_binding=bindings,
                                                       user_agent=_user_agent(request))
        if target.startswith("desktop:"):
            # A desktop sign-in: the system browser only says to go back to the app (MarketPulse S-5).
            page = _desktop_google_page(None if target == "desktop:done" else target.removeprefix("desktop:error:"))
            page.delete_cookie(GOOGLE_DESKTOP_TX_COOKIE, path=_GOOGLE_TX_PATH)
            return page
        redirect = RedirectResponse(safe_redirect_path(target) if not target.startswith("/login?error=")
                                    else target, status_code=303)
        redirect.delete_cookie(GOOGLE_TX_COOKIE, path=_GOOGLE_TX_PATH)
        if issued is not None:
            revoke_presented(service, request)
            _set_session_cookies(redirect, issued, secure=_secure(service, request))
        return redirect

    @router.post("/google/desktop/start", response_model=DesktopGoogleStartResponse,
                 dependencies=[Depends(login_rate_limit)])
    def google_desktop_start(body: DesktopGoogleStartRequest, request: Request) -> DesktopGoogleStartResponse:
        """Start a Google sign-in (or link) for a desktop window; the nonce never leaves the window (S-5)."""
        service = service_for(AuthMode.LOCAL)
        link_user = signed_in(service, request, check_csrf=True).user_id if body.link else None
        try:
            nonce, begin_token = service.start_desktop_google(
                redirect_after=safe_redirect_path(body.next), invite=body.invite or None,
                remember=body.remember, link_user=link_user,
            )
        except AccountError as exc:
            raise account_failure(exc) from exc
        return DesktopGoogleStartResponse(nonce=nonce, begin_url=f"/api/auth/google/desktop/begin?token={begin_token}",
                                          expires_in=DESKTOP_HANDOFF_TTL_SECONDS)

    @router.get("/google/desktop/begin", status_code=302)
    def google_desktop_begin(request: Request, token: str = "") -> Response:
        """Opened once in the system browser: binds it to the login and sends it to Google."""
        service = service_for(AuthMode.LOCAL)
        try:
            started = service.begin_desktop_google(token)
        except AccountError as exc:
            raise account_failure(exc) from exc
        if started is None:
            return _desktop_google_page("login_link_expired")
        url, binding = started
        redirect = RedirectResponse(url, status_code=302)
        redirect.set_cookie(GOOGLE_DESKTOP_TX_COOKIE, binding, max_age=DESKTOP_HANDOFF_TTL_SECONDS, httponly=True,
                            samesite="lax", secure=_secure(service, request), path=_GOOGLE_TX_PATH)
        return redirect

    @router.post("/google/desktop/handoff", response_model=DesktopGoogleHandoffResponse)
    def google_desktop_handoff(body: DesktopGoogleHandoffRequest, request: Request,
                               response: Response) -> DesktopGoogleHandoffResponse:
        """The window trades its nonce for its session once Google answered; 202 while it has not."""
        service = service_for(AuthMode.LOCAL)
        try:
            outcome = service.take_desktop_handoff(body.nonce, user_agent=_user_agent(request))
        except AccountError as exc:
            raise account_failure(exc) from exc
        if outcome is None:
            raise HTTPException(status_code=410, detail="desktop_sign_in_expired")
        if outcome.status == "failed":
            record_auth_rejection(outcome.error or "sign_in_failed")
            raise HTTPException(status_code=409, detail=outcome.error or "sign_in_failed")
        if outcome.status == "pending":
            response.status_code = 202
        elif outcome.status == "signed_in" and outcome.session is not None:
            revoke_presented(service, request)
            _set_session_cookies(response, outcome.session, secure=_secure(service, request))
        return DesktopGoogleHandoffResponse(status=outcome.status, next=safe_redirect_path(outcome.redirect_after))

    @router.get("/sessions", response_model=AuthSessionListResponse)
    def list_sessions(request: Request) -> AuthSessionListResponse:
        """The caller's active sessions (ASVS 3.3.4)."""
        service = service_for()
        principal = signed_in(service, request, check_csrf=False)
        return AuthSessionListResponse(sessions=[
            AuthSessionSummary(id=summary.handle, auth_method=summary.auth_method, created_at=summary.created_at,
                               last_seen_at=summary.last_seen_at, expires_at=summary.expires_at, current=current)
            for summary, current in service.list_sessions(principal)
        ])

    @router.post("/sessions/revoke", response_model=RevokeSessionsResponse, dependencies=[Depends(login_rate_limit)])
    def revoke_sessions(body: RevokeSessionsRequest, request: Request) -> RevokeSessionsResponse:
        """Sign out one other session or all others, after re-authenticating (ASVS 3.3.4)."""
        service = service_for()
        principal = signed_in(service, request, check_csrf=True)
        try:
            revoked = service.revoke_sessions(principal, handle=body.session_id, credential=body.credential)
        except ReauthenticationRequired as exc:
            record_auth_rejection("reauthentication_required")
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        return RevokeSessionsResponse(revoked=revoked)

    @router.post("/logout", status_code=204)
    def logout(request: Request) -> Response:
        service = service_for()
        token = presented_session(request)
        if token:
            principal = service.authenticate_session(token)
            supplied = request.headers.getlist(CSRF_HEADER)
            if principal is not None and (
                len(supplied) != 1
                or not principal.csrf_token
                or not hmac.compare_digest(supplied[0], principal.csrf_token)
            ):
                raise HTTPException(status_code=403, detail="csrf_failed")
            service.logout(token)
        # The browser drops what the app kept locally (drafts, recent events)
        # and its cached responses (ASVS 8.2.3).
        response = Response(status_code=204, headers={"Clear-Site-Data": '"cache", "storage"'})
        _clear_session_cookies(response, secure=_secure(service, request))
        return response

    @router.get("/oidc/login", response_class=RedirectResponse, status_code=302)
    def oidc_login(request: Request, next: str = "/") -> RedirectResponse:
        service = service_for(AuthMode.OIDC)
        url, binding = service.start_oidc_login(redirect_after=next)
        redirect = RedirectResponse(url, status_code=302)
        # Lax, not Strict: the IdP returns the browser with a cross-site
        # top-level navigation, which must still carry this binding.
        redirect.set_cookie(
            OIDC_TX_COOKIE,
            binding,
            max_age=600,
            httponly=True,
            samesite="lax",
            secure=_secure(service, request),
            path=_OIDC_TX_PATH,
        )
        return redirect

    @router.get("/oidc/callback", response_class=RedirectResponse, status_code=303, dependencies=[Depends(login_rate_limit)])
    def oidc_callback(request: Request, code: str = "", state: str = "") -> RedirectResponse:
        service = service_for(AuthMode.OIDC)
        binding = request.cookies.get(OIDC_TX_COOKIE) or ""
        result = service.complete_oidc_login(
            code=code, state=state, browser_binding=binding, user_agent=_user_agent(request)
        )
        if result is None:
            failure = RedirectResponse("/login?error=sign_in_failed", status_code=303)
            failure.delete_cookie(OIDC_TX_COOKIE, path=_OIDC_TX_PATH)
            return failure
        issued, redirect_after = result
        revoke_presented(service, request)
        redirect = RedirectResponse(safe_redirect_path(redirect_after), status_code=303)
        redirect.delete_cookie(OIDC_TX_COOKIE, path=_OIDC_TX_PATH)
        _set_session_cookies(redirect, issued, secure=_secure(service, request))
        return redirect

    return router
