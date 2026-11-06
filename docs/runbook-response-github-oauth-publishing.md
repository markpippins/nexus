# Runbook — publishing the Google OAuth app out of Testing

**Scope.** The `response` app (service `:4400`, UI `:4299`, source in
`/home/codex/dev/response`) mirrors `notifications@github.com` mail into the
`personal` database and from there into the Assembly **`github` forum**. It
authenticates with a Google OAuth **refresh token** and runs on a 15-minute
timer. While the OAuth client's publishing status is **Testing**, Google expires
that refresh token seven days after consent — and the mirror stops, silently,
every week.

This runbook exists because the fix for that is *not in this repository*. It is a
few clicks in the Google Cloud console, and without a written record the next
person re-diagnoses `invalid_grant` from scratch. It has now been diagnosed at
least four times (2026-09-04, 2026-09-08, 2026-09-11 in `audit/`, and again
2026-11-06).

## The rule, from Google

> A Google Cloud Platform project with an OAuth consent screen configured for an
> external user type and a publishing status of "Testing" is issued a refresh
> token expiring in 7 days, unless the only OAuth scopes requested are a subset
> of name, email address, and user profile.
>
> — [Using OAuth 2.0 to Access Google APIs](https://developers.google.com/identity/protocols/oauth2), "Refresh token expiration"

And on the console side:

> Authorizations by a test user will expire seven days from the time of consent.
> If your OAuth client requests an offline access type and receives a refresh
> token, that token will also expire.
>
> — [Manage App Audience](https://support.google.com/cloud/answer/15549945), "Testing"

The app requests `access_type=offline` and nine scopes including
`gmail.readonly`, so it is squarely in the expiring case: the `userinfo.*`
exemption does not apply.

## What is wired up

Public/structural facts only — no secrets here, and none belong in this file.

| Item | Value |
|---|---|
| OAuth client ID | `9000965993-gdlnbknpmpoj8ik92j6jb1a23kmt3tbu.apps.googleusercontent.com` |
| Redirect URI | `http://localhost:4299/api/auth/callback` |
| Consent URL (live) | `GET http://localhost:4299/api/auth/url` |
| Token store | `personal.response.oauth_tokens` (written by the UI callback) |
| Sync state | `personal.response.mail_sync_state`, feed `github-notifications-v2` |
| Sync unit | `response-github-sync.timer` → `.service` (**user** manager, see traps) |
| Sync log | `/home/codex/dev/response/logs/sync.log` (appended, never rotated) |
| Credentials | `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` in `response/service/.env` |

Requested scopes: `gmail.readonly`, `gmail.send`, `gmail.modify`, `contacts`,
`calendar.events`, `drive.file`, `drive.readonly`, `userinfo.email`,
`userinfo.profile`.

> **Only two of those are needed for this ingest**: `gmail.readonly` (list and
> read the notifications) and `gmail.send` (the UI's reply path). The other
> seven belong to unrelated response-app features. Trimming them before
> verification is worth considering — every sensitive/restricted scope widens
> the verification review and the "unverified app" data-access warning.

## Recognising the failure

Symptom: the forum stops receiving threads; no error appears anywhere obvious.
The timer keeps firing and the unit keeps exiting 1, which produces no alert —
`nexus/bin/hourly_maintenance.py` only inspects port-based service status
(`start-nexus-services.sh status`), and a oneshot that fails every 15 minutes is
invisible to that model.

```bash
# Unit state — note the user manager
systemctl --user list-timers response-github-sync.timer --no-pager
systemctl --user show -p Result -p ExecMainStatus \
  -p ExecMainExitTimestamp response-github-sync.service

# The real error (journald only carries systemd's own lines)
tail -30 /home/codex/dev/response/logs/sync.log
```

A failing run's summary looks like this — `listed: 0`, watermark unchanged:

```json
{
  "feed": "github-notifications-v2",
  "watermarkBefore": 1791591449000,
  "watermarkAfter": 1791591449000,
  "windowAfter": null,
  "listed": 0, "fetched": 0, "newMessages": 0, "stored": 0, "posted": 0,
  "authError": "invalid_grant"
}
```

`auth: invalid_grant` in `mail_sync_state.last_error` plus a **frozen watermark**
is the signature. Decode the watermark before assuming anything:

```bash
python3 -c "import datetime;print(datetime.datetime.fromtimestamp(1791591449000/1000, datetime.UTC))"
# 2026-10-10 00:17:29+00:00  — the moment the token died
```

### Before you publish: rule out the other causes of `invalid_grant`

Publishing is the fix only for the 7-day cliff. All of these also invalidate a
refresh token, and none is fixed by publishing:

- **A password change on the Google account** — invalidates refresh tokens
  carrying Gmail scopes. Very common, and it looks identical.
- **More than 100 live refresh tokens** for that account + client ID; issuing a
  new one silently invalidates the oldest.
- The user revoked access at <https://myaccount.google.com/permissions>.
- A Workspace admin restricted the scopes (`admin_policy_enforced`), or the
  account is in Advanced Protection.

If the token died exactly 7 days (± minutes) after consent, it is the cliff. If
it died at some arbitrary interval, look at the list above first.

## Fix A — publish the app (External projects)

1. Google Cloud console → project owning the client above → **Google Auth
   Platform** (older name: *OAuth consent screen*) → **Audience**.
2. Under **Publishing status: Testing**, click **Publish app**.
3. Confirm. Status becomes **In production**.

What changes, and what does not:

- **Fixed:** the 7-day refresh-token expiry no longer applies. This is the whole
  point.
- **Not fixed:** nothing else. Existing tokens keep working; you do **not** need
  to re-consent to get a non-expiring token, though the next consent you do
  grant will be durable.
- **New consequence:** the app becomes available to any Google Account, and
  because it requests sensitive/restricted scopes and is unverified, users see
  the **"Google hasn't verified this app"** screen, and a **user cap** applies to
  unverified apps. For a single-operator tool that is acceptable — you click
  *Advanced → Go to (unsafe)* once.
- **Verification** is only needed to remove that warning or if Google's
  [verification criteria](https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification)
  apply. It is not required to stop the 7-day expiry. Do not let the prospect of
  a verification review block a publish that fixes the outage.

## Fix B — Internal user type (only if a Workspace domain owns the project)

If the account is a Google Workspace user and the project is owned by that
organisation, set **User Type: Internal** instead. Internal apps need no
verification, show no unverified warning, and are not subject to the 7-day
expiry. This is the cleanest option when available; it is unavailable for
`@gmail.com` accounts.

Changing user type can invalidate existing authorisations — plan a re-consent in
the same sitting.

## Fix C — stay in Testing (status quo, weekly re-auth)

Accept the expiry and re-authorise every 7 days. The consent URL is served live:

```bash
curl -s http://localhost:4299/api/auth/url     # open the returned url
```

Completing consent POSTs the tokens to `/api/oauth/store` (loopback-only) and
rewrites the stored refresh token. **No manual replay is needed** — the next
timer tick derives its window from the watermark, so a long gap widens the
window and the next successful run backfills it. That is by design, and it was
verified on 2026-11-06: the run after re-auth advanced the watermark from
`2026-10-10T00:17Z` to `2026-11-06T21:08Z` and posted 268 threads from the
27-day gap.

Do not advance or reset the watermark by hand to "make it catch up". It is the
only thing that knows what has and has not been stored; the recovery is
automatic.

## Verifying the fix actually took

Publishing does not change today's behaviour — it changes what happens on day
eight. So the proof is a **survival test**, not a green run:

```bash
# Now: record the consent time and confirm a green run
systemctl --user show -p Result -p ExecMainExitTimestamp response-github-sync.service
tail -20 /home/codex/dev/response/logs/sync.log | grep -E 'authError|watermarkAfter'

# Day 8+: same two commands. Success looks like
#   Result=success, no "authError" key in the summary,
#   watermarkAfter advancing.
```

A published app that still fails on day 8 means the cause was **not** the cliff —
go back to the ruled-out list above.

## Traps worth knowing

- **The units are user units.** `systemctl status response-github-sync.service`
  reports *"Unit could not be found"* because it is not a system unit. Always
  query with `--user`. They live in `~/.config/systemd/user/`.
- **journald does not show the app's output.** `StandardOutput`/`StandardError`
  are redirected to `logs/sync.log`, so `journalctl` shows only systemd's
  start/failure lines. Read the log file.
- **`logs/sync.log` is append-only and unrotated** — ~990 KB by 2026-11-06,
  dominated by repeated `Auth expired` summaries. It is a diagnostic aid, not a
  bounded log; size is not an indicator of anything.
- **The forum clock runs ~5 h ahead of UTC.** `createdAt` on forum threads is not
  comparable to nebula UTC timestamps, and `createdAt` is *ingestion* time, not
  the mail's date. A backfilled month all appears on the recovery day. Do not
  conclude a gap in the forum's date histogram means mail was skipped.
- **`journalctl --since "16:00"` is parsed in host-local time** (UTC−5 on
  titanium), not UTC. Use `--since -15min` or an explicit UTC stamp.
- **Merge notifications never existed in this feed.** GitHub does not mail you
  about pull requests you merged yourself. The forum carries CI/run events only.
  For merge confirmation use `gh pr view`, not the forum.
