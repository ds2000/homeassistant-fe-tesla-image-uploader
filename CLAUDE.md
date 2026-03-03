# Claude Project: Tesla Card Image Uploader

## Project Overview

This repo powers the community image contribution pipeline for the
[homeassistant-fe-tesla](https://github.com/ds2000/homeassistant-fe-tesla) card.

Contributors submit car images via a GitHub Pages web app. Submissions are
automatically validated, processed into card-ready overlays, and
opened as PRs for review before being merged into the image library.

**Repo:** https://github.com/ds2000/homeassistant-fe-tesla-image-uploader
**GitHub Pages URL:** https://ds2000.github.io/homeassistant-fe-tesla-image-uploader
**Related card repo:** https://github.com/ds2000/homeassistant-fe-tesla

---

## Single Source of Truth: `models.json`

The card repo hosts `models.json` at its root — the single source of truth for all
models, variants, colours, and display names. Both repos consume it:

- **Card** imports it at build time (`src/models.js`, `src/recolour.js`)
- **Uploader** fetches it at runtime from the card repo's raw URL

```json
{
  "models": [
    {
      "id": "3",
      "name": "Model 3",
      "variants": [
        {
          "id": "3.1",
          "label": "2017–2023",
          "colours": [
            { "id": "red_multi_coat", "name": "Red Multi-Coat", "swatch": "#c41e28", "hasImages": true },
            { "id": "deep_blue_metallic", "name": "Deep Blue Metallic", "swatch": "#223873", "hasImages": true }
          ]
        }
      ]
    }
  ]
}
```

- Colour `id` = directory name in card image paths (e.g. `3/3.1/red_multi_coat/`)
- `hasImages: true` = card has processed images on disk for this colour
- Absent `hasImages` = images not yet available (implicitly false)

---

## Repository Structure

```
/
├── CLAUDE.md
├── README.md
├── status.json                        ← flat availability map (see below)
├── docs/                              ← GitHub Pages root
│   ├── index.html                     ← submission web app
│   ├── submit.js                      ← form logic, API calls
│   ├── testbed.html                   ← local testbed UI
│   ├── testbed.js                     ← testbed logic
│   ├── styles.css                     ← Tesla dark theme styling
│   └── assets/
│       └── guides/                    ← screenshot guide images
├── scripts/
│   ├── validate_submission.py         ← file presence + PNG checks
│   ├── process_screenshots.py         ← crop, split, overlay generation
│   ├── process_submission.py          ← end-to-end submission wrapper
│   └── local_testbed.py              ← local dev server
└── .github/
    └── workflows/
        ├── process-submission.yml     ← triggered on submissions/* branch push
        ├── send-verification.yml      ← triggered by workflow_dispatch for email
        └── update-status.yml          ← updates status.json on PR merge/close
```

---

## status.json Schema

Flat map keyed by `{model_id}/{variant_id}/{colour_id}`. Any model/variant/colour
combo in `models.json` without a `status.json` entry is implicitly "available".

```json
{
  "3/3.1/red_multi_coat": { "status": "complete" },
  "3/3.1/deep_blue_metallic": { "status": "pending", "pr": 12 }
}
```

**Status values:**
- _(absent)_ — not yet submitted, shown in dropdown as available
- `pending` — PR open, greyed out with "Under review" tooltip, `pr` field has PR number
- `complete` — merged and available in card, greyed out with "Already available" tooltip

**NEVER manually edit status.json** — it is updated automatically by GitHub Actions:
- `pending` set when a submission PR is opened
- `complete` set when a submission PR is merged
- Key removed if a submission PR is closed without merging (reverts to available)

---

## Required Screenshots Per Submission

Every submission must include all of the following PNG files.
Missing files = automatic rejection by the validation script.

### Offcharge (9 files)

| Filename | Description |
|---|---|
| `closed.png` | Car, all closed, parked |
| `cp.png` | Charge port open |
| `cp_ft.png` | Frunk and trunk open with charge port |
| `rt.png` | Trunk open |
| `front_doors.png` | Both front doors open |
| `rear_doors.png` | Both rear doors open |
| `all_doors.png` | All four doors open simultaneously |
| `top_controls.png` | Controls panel screenshot |
| `top_climate.png` | Climate panel screenshot |

### On-charge (6 files)

| Filename | Description |
|---|---|
| `oc_closed.png` | Car on charge, all closed |
| `oc_cp_ft.png` | On charge, frunk and trunk open |
| `oc_rt.png` | On charge, trunk open |
| `oc_front_doors.png` | On charge, both front doors open |
| `oc_rear_doors.png` | On charge, both rear doors open |
| `oc_all_doors.png` | On charge, all four doors open simultaneously |

All images must be **PNG** (validated via PNG header signature).

---

## GitHub Actions Workflows

### `process-submission.yml`
Triggered: push to any `submissions/*` branch

Steps:
1. Fetch `models.json` from card repo
2. Parse model/variant/colour from submission directory name
3. `validate_submission.py` — check all 15 files present, all PNG
4. `process_screenshots.py` — crop, split combined doors, generate overlays (offcharge + oncharge)
5. Open PR from submission branch → main
6. Update `status.json` → set combination to `pending` with PR number
7. Commit status.json update

### `send-verification.yml`
Triggered: `workflow_dispatch` from GitHub Pages app via GitHub API

Inputs: `email`, `token_hash`, `model`, `variant`, `colour`

Steps:
1. Fetch `models.json` from card repo
2. Validate inputs against models.json
3. Call Resend API (key from `RESEND_API_KEY` secret) to send verification email
4. Email contains link back to GitHub Pages with `?token=xxx&model=xxx&variant=xxx&colour=xxx`

### `update-status.yml`
Triggered: PR closed (merged or rejected)

Steps:
1. Fetch `models.json` from card repo, parse branch name
2. If merged → set status to `complete`
3. If closed without merge → remove key from status.json (reverts to available)
4. Commit status.json update

---

## Email Verification Flow

The GitHub Pages app is fully static — no backend. Email is sent via
GitHub Actions triggered by the GitHub API.

```
1. User fills form (model/variant/colour/email)
2. App generates a random token + HMAC signature using Web Crypto API
   - token = crypto.randomUUID()
   - signature = HMAC-SHA256(token + model + variant + colour, PUBLIC_HMAC_SALT)
   - PUBLIC_HMAC_SALT is a non-secret string hardcoded in submit.js
     (it prevents trivial forgery, not a security secret)
3. App calls GitHub API to trigger send-verification workflow:
   POST https://api.github.com/repos/ds2000/homeassistant-fe-tesla-image-uploader
        /actions/workflows/send-verification.yml/dispatches
   Headers: Authorization: Bearer {GITHUB_PAT}  ← stored as JS const in submit.js
   Body: { ref: "main", inputs: { email, token_hash, model, variant, colour } }
4. GitHub Actions calls Resend → sends email with link:
   https://ds2000.github.io/homeassistant-fe-tesla-image-uploader
   ?token=xxx&sig=xxx&model=3&variant=3.1&colour=red_multi_coat
5. User clicks link → app validates HMAC signature client-side
6. If valid → upload form unlocked for that specific combination
7. Token stored in sessionStorage to survive page refresh
```

### GitHub PAT Scope
The PAT stored in `submit.js` only needs `actions:write` scope — it cannot
read code, write to the repo, or do anything else. It is safe to commit.
Generate at: GitHub → Settings → Developer Settings → Personal Access Tokens → Fine-grained
Permissions: Actions (Read and Write), nothing else.

---

## Submission Branch Naming

```
submissions/{model_id}-{variant_id}-{colour_id}-{short_token}
```

Example: `submissions/3-3.1-red_multi_coat-a3f9`

The short token (4 hex chars) prevents branch name collisions for duplicate attempts.

---

## Web App Design

Matches Tesla app aesthetic — consistent with the card itself.

- Dark background `#0d0d0d`, white text, Tesla red `#e82127` accents
- Font: `'Gotham', 'Century Gothic', system-ui`
- Single page, no framework, vanilla JS only
- Steps shown as a visual progress indicator (1. Select → 2. Verify → 3. Upload)
- Unavailable combinations shown greyed out with tooltip explaining why
- Upload area shows which screenshots are still needed vs uploaded
- Mobile-friendly (contributors may be photographing on their phone)

---

## What NOT to Do

- Do not store `RESEND_API_KEY` anywhere except GitHub Secrets
- Do not use any JS framework (React, Vue) — vanilla JS only
- Do not manually edit `status.json` — always via GitHub Actions
- Do not skip the HMAC validation on the verification link — always verify before unlocking upload
- Do not duplicate model/variant/colour definitions — always derive from `models.json`

---

## Secrets Required (set in GitHub repo Settings → Secrets)

| Secret name | Value |
|---|---|
| `RESEND_API_KEY` | From resend.com dashboard |
| `GH_BOT_TOKEN` | PAT with actions:write for committing status.json updates |

---

## Reference Links

- [Resend API docs](https://resend.com/docs)
- [GitHub Actions workflow_dispatch API](https://docs.github.com/en/rest/actions/workflows#create-a-workflow-dispatch-event)
- [Web Crypto API — HMAC](https://developer.mozilla.org/en-US/docs/Web/API/SubtleCrypto/sign)
