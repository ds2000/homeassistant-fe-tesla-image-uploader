# Claude Project: Tesla Card Image Uploader

## Project Overview

This repo powers the community image contribution pipeline for the
[homeassistant-fe-tesla](https://github.com/ds2000/homeassistant-fe-tesla) card.

Contributors submit car images via a GitHub Pages web app. Submissions are
automatically validated, aligned against reference images using OpenCV, and
opened as PRs for review before being merged into the image library.

**Repo:** https://github.com/ds2000/homeassistant-fe-tesla-image-uploader
**GitHub Pages URL:** https://ds2000.github.io/homeassistant-fe-tesla-image-uploader
**Related card repo:** https://github.com/ds2000/homeassistant-fe-tesla

---

## Repository Structure

```
/
├── CLAUDE.md
├── README.md
├── status.json                        ← availability map, read by the web app
├── docs/                              ← GitHub Pages root
│   ├── index.html                     ← submission web app (single file)
│   ├── submit.js                      ← form logic, API calls
│   ├── styles.css                     ← Tesla dark theme styling
│   └── assets/
│       └── reference-preview.png      ← shows contributors what good looks like
├── reference/                         ← canonical reference images (Model 3 red)
│   └── model3/
│       ├── base.png
│       ├── trunk_base.png
│       ├── frunk.png
│       ├── fl.png
│       ├── fr.png
│       ├── rl.png
│       ├── rr.png
│       ├── flrl.png
│       ├── plug.png
│       ├── charging.png
│       └── driving.png
├── images/                            ← approved, processed images
│   └── {model}/
│       └── {year_range}/
│           └── {colour}/
│               └── *.png
├── scripts/
│   ├── align_images.py                ← OpenCV feature matching + transform
│   ├── validate_submission.py         ← file presence + dimension checks
│   ├── desaturate_body.py             ← HSV body panel desaturation
│   └── generate_preview.py            ← composite preview for PR review
└── .github/
    └── workflows/
        ├── process-submission.yml     ← triggered on submissions/* branch push
        ├── send-verification.yml      ← triggered by workflow_dispatch for email
        └── update-status.yml          ← updates status.json on PR merge/open
```

---

## status.json Schema

This file is the single source of truth for what's available, pending, or complete.
It is committed to the repo and read directly by the GitHub Pages app at load time.

```json
{
  "models": {
    "model3": {
      "label": "Model 3",
      "years": {
        "2017-2024": {
          "label": "2017–2024 (pre-refresh)",
          "colours": {
            "midnight_black": { "status": "complete", "pr": null },
            "pearl_white": { "status": "available", "pr": null },
            "deep_blue_metallic": { "status": "pending", "pr": 12 },
            "red_multi_coat": { "status": "complete", "pr": null },
            "silver_metallic": { "status": "available", "pr": null }
          }
        },
        "2024+": {
          "label": "2024+ (Highland refresh)",
          "colours": { ... }
        }
      }
    },
    "modely": { ... },
    "models": { ... },
    "modelx": { ... },
    "cybertruck": { ... }
  }
}
```

**Status values:**
- `available` — not yet submitted, shown in dropdown
- `pending` — PR open, greyed out with "Under review" tooltip, `pr` field has PR number
- `complete` — merged and available in card, greyed out with "Already available" tooltip

**NEVER manually edit status.json** — it is updated automatically by GitHub Actions:
- `pending` set when a submission PR is opened
- `complete` set when a submission PR is merged
- `available` restored if a submission PR is closed without merging

---

## Required Image Layers Per Submission

Every submission must include all of the following transparent PNG files.
Missing files = automatic rejection by the validation script.

| Filename | Description |
|---|---|
| `base.png` | Car, all closed, parked |
| `trunk_base.png` | Car with trunk open (different silhouette) |
| `frunk.png` | Frunk open overlay (transparent background) |
| `fl.png` | Front-left door open overlay |
| `fr.png` | Front-right door open overlay |
| `rl.png` | Rear-left door open overlay |
| `rr.png` | Rear-right door open overlay |
| `flrl.png` | Both driver-side doors open (special combined overlay) |
| `plug.png` | Charge cable visible overlay |
| `charging.png` | Charging active glow overlay |
| `driving.png` | Full replacement image when vehicle is driving |

All images must be **PNG with transparency** (RGBA), **not JPEG**.
Dimensions must match the reference images exactly after alignment.

---

## CV Alignment Pipeline (`scripts/align_images.py`)

Uses OpenCV SIFT feature matching to align submitted images against the
reference Model 3 images.

### Algorithm
1. Load submitted `base.png` and reference `reference/model3/base.png`
2. Detect SIFT keypoints in both images
3. Match keypoints using FLANN matcher
4. Filter matches using Lowe's ratio test (threshold: 0.75)
5. If good matches >= 10: compute homography with RANSAC
6. Apply perspective transform to submitted image
7. Apply same transform matrix to ALL other submitted layers
8. Output confidence score (0.0–1.0) based on match count and inlier ratio

### Thresholds
- **confidence >= 0.7** → auto-align and proceed
- **confidence 0.4–0.7** → align but flag for manual review in PR comment
- **confidence < 0.4** → reject, comment on PR with reason and visual diff

### CLI Usage
```bash
python scripts/align_images.py \
  --submission-dir submissions/model3-2017-2024-midnight_black/ \
  --reference-dir reference/model3/ \
  --output-dir aligned/ \
  --confidence-report report.json
```

### Rules
- NEVER distort images (no shear) — only scale, rotate, translate
- If transform requires scale change > 20% → reject (wrong crop)
- If transform requires rotation > 15° → reject (wrong orientation)
- All layers get the SAME transform matrix derived from `base.png` matching

---

## GitHub Actions Workflows

### `process-submission.yml`
Triggered: push to any `submissions/*` branch

Steps:
1. `validate_submission.py` — check all 11 files present, all RGBA PNG
2. `align_images.py` — feature match against reference, apply transform
3. `desaturate_body.py` — desaturate body panels for colour tinting
4. `generate_preview.py` — create composite preview image showing all states
5. Open PR from submission branch → main
6. Attach preview image as PR comment
7. Update `status.json` → set combination to `pending` with PR number
8. Commit status.json update

### `send-verification.yml`
Triggered: `workflow_dispatch` from GitHub Pages app via GitHub API

Inputs: `email`, `token_hash`, `model`, `year_range`, `colour`

Steps:
1. Validate token_hash format
2. Call Resend API (key from `RESEND_API_KEY` secret) to send verification email
3. Email contains link back to GitHub Pages with `?token=xxx&model=xxx&...`

### `update-status.yml`
Triggered: PR closed (merged or rejected)

Steps:
1. If merged → set status to `complete`, clear `pr` field
2. If closed without merge → set status back to `available`, clear `pr` field
3. Commit status.json update

---

## Email Verification Flow

The GitHub Pages app is fully static — no backend. Email is sent via
GitHub Actions triggered by the GitHub API.

```
1. User fills form (model/year/colour/email)
2. App generates a random token + HMAC signature using Web Crypto API
   - token = crypto.randomUUID()
   - signature = HMAC-SHA256(token + model + year + colour, PUBLIC_HMAC_SALT)
   - PUBLIC_HMAC_SALT is a non-secret string hardcoded in submit.js
     (it prevents trivial forgery, not a security secret)
3. App calls GitHub API to trigger send-verification workflow:
   POST https://api.github.com/repos/ds2000/homeassistant-fe-tesla-image-uploader
        /actions/workflows/send-verification.yml/dispatches
   Headers: Authorization: Bearer {GITHUB_PAT}  ← stored as JS const in submit.js
   Body: { ref: "main", inputs: { email, token_hash, model, year_range, colour } }
4. GitHub Actions calls Resend → sends email with link:
   https://ds2000.github.io/homeassistant-fe-tesla-image-uploader
   ?token=xxx&sig=xxx&model=model3&year=2017-2024&colour=midnight_black
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
submissions/{model}-{year_range}-{colour}-{short_token}
```

Example: `submissions/model3-2017-2024-midnight_black-a3f9`

The short token (4 hex chars) prevents branch name collisions for duplicate attempts.

---

## Web App Design

Matches Tesla app aesthetic — consistent with the card itself.

- Dark background `#0d0d0d`, white text, Tesla red `#e82127` accents
- Font: `'Gotham', 'Century Gothic', system-ui`
- Single page, no framework, vanilla JS only
- Steps shown as a visual progress indicator (1. Select → 2. Verify → 3. Upload)
- Unavailable combinations shown greyed out with tooltip explaining why
- Upload area shows which layers are still needed vs uploaded
- Live preview composite updates as user uploads each layer
- Mobile-friendly (contributors may be photographing on their phone)

---

## What NOT to Do

- ❌ Do not store `RESEND_API_KEY` anywhere except GitHub Secrets
- ❌ Do not use any JS framework (React, Vue) — vanilla JS only
- ❌ Do not accept JPEG uploads — PNG with transparency only
- ❌ Do not manually edit `status.json` — always via GitHub Actions
- ❌ Do not apply different transform matrices to different layers — one matrix from base.png applied to all
- ❌ Do not commit reference images as JPEG — they must be PNG
- ❌ Do not skip the HMAC validation on the verification link — always verify before unlocking upload

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
- [OpenCV SIFT Python docs](https://docs.opencv.org/4.x/da/df5/tutorial_py_sift_intro.html)
- [Web Crypto API — HMAC](https://developer.mozilla.org/en-US/docs/Web/API/SubtleCrypto/sign)
- [threesquare/Tesla-doors-visual](https://github.com/threesquare/Tesla-doors-visual) — layered image inspiration