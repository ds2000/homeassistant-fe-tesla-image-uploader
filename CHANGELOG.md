# Changelog

## [0.3.0] - 2026-03-04

### Added
- **Verify button** on the upload page -- runs image checks (missing screenshots, cable detection, duplicate detection) at any time before submitting
- **Contributor prompt** -- on submit, users can opt in to be credited in the project's contributors section (name, GitHub username, or email); saved as `contributor.json` alongside the submission
- **Thank-you message** -- success screen thanks the contributor and confirms they'll be listed when merged
- **Card button test** (`tests/test_card_buttons.py`) -- static analysis verifying every `_svc()` call in the tesla-card source uses the correct HA domain, service, and entity
- **E2E submission test** (`tests/test_e2e_submission.py`) -- uploads test images via the GitHub API, creates a submission branch, waits for the workflow, and verifies the PR

### Changed
- Submit and Verify buttons now sit side-by-side in the upload step
- Upload success message rewritten with a personalised thank-you

## [0.2.0] - 2026-03-03

### Added
- **All-doors screenshots** -- `all_doors.png` (offcharge) and `oc_all_doors.png` (oncharge) now required, showing all four doors open simultaneously
- **Local testbed server** (`scripts/local_testbed.py`) -- full local dev environment with API endpoints, auto-populate from dev screenshots, submission processing, and promote-to-card workflow
- **Testbed UI** (`docs/testbed.html`) -- browser-based upload and processing preview without needing GitHub PATs

### Changed
- Submission requirement increased from 13 to 15 screenshots (9 offcharge + 6 oncharge)
- Guide images added for the two new all-doors screenshots

## [0.1.0] - 2026-02-27

### Added
- **Image processing pipeline** (`scripts/process_screenshots.py`) -- automated crop, alignment, overlay generation from Tesla app screenshots
- **Combined-door splitting** -- contributors submit 2 combined-door shots instead of 4 individual; pipeline auto-splits into near/far overlays
- **Validation script** (`scripts/validate_submission.py`) -- file presence and PNG header checks
- **GitHub Pages web app** (`docs/`) -- model/variant/colour selection, email verification, screenshot upload wizard
- **GitHub Actions workflows** -- `process-submission.yml` (validate + process on push), `send-verification.yml` (email via Resend), `update-status.yml` (status.json on PR merge/close)
- **Email verification flow** -- HMAC-signed tokens via GitHub Actions + Resend API
- **status.json** -- flat availability map tracking pending/complete submissions
