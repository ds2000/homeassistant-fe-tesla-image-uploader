#!/usr/bin/env python3
"""
E2E Submission Pipeline Test

Uploads test images to GitHub via the Git Data API, creating a submission
branch that triggers the process-submission workflow.

Usage:
    python3 tests/test_e2e_submission.py
    python3 tests/test_e2e_submission.py --model 3 --variant 3.1 --colour red_multi_coat
    python3 tests/test_e2e_submission.py --dry-run   # validate locally only
"""

import argparse
import base64
import json
import subprocess
import sys
import time
import uuid
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────

REPO = 'ds2000/homeassistant-fe-tesla-image-uploader'
BASE_BRANCH = 'TESUPL0001'
TEST_IMAGES_DIR = Path(__file__).resolve().parent / 'test_images'

# Map: local subpath (under {model}/{variant}/{colour}/) → submission filename
FILE_MAP = {
    'offcharge/closed.png':        'closed.png',
    'offcharge/chargeport.png':    'cp.png',
    'offcharge/frunk.png':         'cp_ft.png',
    'offcharge/trunk.png':         'rt.png',
    'offcharge/front_doors.png':   'front_doors.png',
    'offcharge/rear_doors.png':    'rear_doors.png',
    'offcharge/all_doors.png':     'all_doors.png',
    'offcharge/controls.png':      'top_controls.png',
    'offcharge/climate.png':       'top_climate.png',
    'oncharge/oc_closed.png':      'oc_closed.png',
    'oncharge/oc_frunk.png':       'oc_cp_ft.png',
    'oncharge/oc_trunk.png':       'oc_rt.png',
    'oncharge/oc_front_doors.png': 'oc_front_doors.png',
    'oncharge/oc_rear_doors.png':  'oc_rear_doors.png',
    'oncharge/oc_all_doors.png':   'oc_all_doors.png',
}

WORKFLOW_TIMEOUT = 300  # seconds

# ── ANSI colours ────────────────────────────────────────────────────────

G = '\033[92m'
R = '\033[91m'
Y = '\033[93m'
B = '\033[1m'
DIM = '\033[2m'
X = '\033[0m'

# ── Helpers ─────────────────────────────────────────────────────────────


def gh_api(endpoint, method='GET', payload=None):
    """Call GitHub API via gh CLI. Returns parsed JSON."""
    cmd = ['gh', 'api']
    if method != 'GET':
        cmd += ['--method', method]
    cmd.append(endpoint)

    input_data = None
    if payload:
        cmd += ['--input', '-']
        input_data = json.dumps(payload)

    result = subprocess.run(
        cmd, capture_output=True, text=True, input=input_data
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"gh api {method} {endpoint} failed:\n{result.stderr.strip()}"
        )
    return json.loads(result.stdout) if result.stdout.strip() else {}


def check_prerequisites(model, variant, colour):
    """Verify gh auth and that all 15 test images exist."""
    # gh CLI auth
    result = subprocess.run(
        ['gh', 'auth', 'status'], capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"  {R}ERROR{X}: gh CLI not authenticated. Run: gh auth login")
        sys.exit(1)
    print(f"  {G}ok{X} gh CLI authenticated")

    # Test images
    img_dir = TEST_IMAGES_DIR / model / variant / colour
    missing = []
    for subpath in FILE_MAP:
        if not (img_dir / subpath).exists():
            missing.append(subpath)

    if missing:
        print(f"  {R}ERROR{X}: Missing {len(missing)} test images in {img_dir}:")
        for m in missing:
            print(f"      {m}")
        sys.exit(1)

    print(f"  {G}ok{X} All {len(FILE_MAP)} test images found")
    return img_dir


def create_blobs(img_dir):
    """Upload each image as a Git blob. Returns {submission_name: sha}."""
    blobs = {}
    for subpath, target_name in FILE_MAP.items():
        local_path = img_dir / subpath
        content_b64 = base64.b64encode(local_path.read_bytes()).decode()

        data = gh_api(
            f'/repos/{REPO}/git/blobs', 'POST',
            {'content': content_b64, 'encoding': 'base64'},
        )
        blobs[target_name] = data['sha']
        size_kb = local_path.stat().st_size / 1024
        print(f"  {G}ok{X} {target_name} ({size_kb:.0f} KB) → {data['sha'][:8]}")

    return blobs


def wait_for_workflow(branch):
    """Poll for the process-submission workflow run on the branch."""
    print(f"\n{B}Step 6: Wait for workflow{X}")
    start = time.time()
    run_id = None

    while time.time() - start < WORKFLOW_TIMEOUT:
        result = subprocess.run(
            ['gh', 'run', 'list', '--repo', REPO, '--branch', branch,
             '--workflow', 'process-submission.yml',
             '--json', 'databaseId,status,conclusion', '--limit', '1'],
            capture_output=True, text=True,
        )
        if result.returncode == 0 and result.stdout.strip():
            runs = json.loads(result.stdout)
            if runs:
                run = runs[0]
                run_id = run['databaseId']
                status = run['status']
                conclusion = run.get('conclusion', '')
                elapsed = int(time.time() - start)

                if status == 'completed':
                    if conclusion == 'success':
                        print(f"  {G}ok{X} Workflow completed ({elapsed}s)")
                    else:
                        print(f"  {R}FAIL{X} Workflow {conclusion} ({elapsed}s)")
                        print(
                            f"    View: gh run view {run_id} --repo {REPO}"
                        )
                    return run_id
                else:
                    print(
                        f"  {DIM}… {status} ({elapsed}s){X}",
                        end='\r', flush=True,
                    )

        time.sleep(10)

    print(f"\n  {Y}TIMEOUT{X}: Workflow did not complete in {WORKFLOW_TIMEOUT}s")
    if run_id:
        print(f"    View: gh run view {run_id} --repo {REPO}")
    return run_id


def verify_pr(branch):
    """Check that a PR was created from the submission branch."""
    result = subprocess.run(
        ['gh', 'pr', 'list', '--repo', REPO, '--head', branch,
         '--json', 'number,title,url', '--limit', '1'],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        print(f"  {R}FAIL{X}: Could not list PRs")
        return None

    prs = json.loads(result.stdout)
    if not prs:
        print(f"  {Y}WARN{X}: No PR found for branch {branch}")
        return None

    pr = prs[0]
    print(f"  {G}ok{X} PR #{pr['number']}: {pr['title']}")
    print(f"    {pr['url']}")
    return pr


# ── Main ────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description='E2E submission pipeline test'
    )
    parser.add_argument('--model', default='3')
    parser.add_argument('--variant', default='3.1')
    parser.add_argument('--colour', default='red_multi_coat')
    parser.add_argument(
        '--dry-run', action='store_true',
        help='Validate test images locally without making API calls',
    )
    args = parser.parse_args()

    token = uuid.uuid4().hex[:4]
    branch = f"submissions/{args.model}-{args.variant}-{args.colour}-{token}"
    sub_dir = f"submissions/{args.model}/{args.variant}/{args.colour}"

    print(f"\n{B}E2E Submission Pipeline Test{X}")
    print(f"Target:  {args.model}/{args.variant}/{args.colour}")
    print(f"Branch:  {branch}")
    print()

    # ── Step 1: Prerequisites ──

    print(f"{B}Step 1: Prerequisites{X}")
    img_dir = check_prerequisites(args.model, args.variant, args.colour)

    if args.dry_run:
        print(f"\n{G}Dry run complete{X} — all {len(FILE_MAP)} images validated.")
        sys.exit(0)

    # ── Step 2: Get HEAD SHA ──

    print(f"\n{B}Step 2: Get HEAD SHA{X}")
    ref_data = gh_api(f'/repos/{REPO}/git/ref/heads/{BASE_BRANCH}')
    head_sha = ref_data['object']['sha']
    print(f"  {G}ok{X} HEAD: {head_sha[:12]}")

    # ── Step 3: Get base tree ──

    print(f"\n{B}Step 3: Get base tree{X}")
    commit_data = gh_api(f'/repos/{REPO}/git/commits/{head_sha}')
    base_tree = commit_data['tree']['sha']
    print(f"  {G}ok{X} Tree: {base_tree[:12]}")

    # ── Step 4: Create blobs ──

    print(f"\n{B}Step 4: Create blobs ({len(FILE_MAP)} images){X}")
    blobs = create_blobs(img_dir)

    # ── Step 5: Create tree + commit + branch ──

    print(f"\n{B}Step 5: Create tree, commit, branch{X}")

    tree_items = [
        {
            'path': f'{sub_dir}/{name}',
            'mode': '100644',
            'type': 'blob',
            'sha': sha,
        }
        for name, sha in blobs.items()
    ]
    tree_data = gh_api(
        f'/repos/{REPO}/git/trees', 'POST',
        {'base_tree': base_tree, 'tree': tree_items},
    )
    new_tree = tree_data['sha']
    print(f"  {G}ok{X} Tree:   {new_tree[:12]}")

    commit = gh_api(
        f'/repos/{REPO}/git/commits', 'POST',
        {
            'message': (
                f'test: e2e submission '
                f'{args.model}/{args.variant}/{args.colour}'
            ),
            'tree': new_tree,
            'parents': [head_sha],
        },
    )
    commit_sha = commit['sha']
    print(f"  {G}ok{X} Commit: {commit_sha[:12]}")

    gh_api(
        f'/repos/{REPO}/git/refs', 'POST',
        {'ref': f'refs/heads/{branch}', 'sha': commit_sha},
    )
    print(f"  {G}ok{X} Branch: {branch}")

    # ── Step 6: Wait for workflow ──

    run_id = wait_for_workflow(branch)

    # ── Step 7: Verify PR ──

    print(f"\n{B}Step 7: Verify PR{X}")
    pr = verify_pr(branch)

    # ── Summary ──

    print(f"\n{B}{'─' * 50}{X}")
    print(f"{B}Summary{X}")
    print(f"  Branch:   {branch}")
    if run_id:
        print(f"  Workflow: gh run view {run_id} --repo {REPO}")
    if pr:
        print(f"  PR:       {pr['url']}")
        print(f"\n  {G}E2E test passed{X} — review and merge the PR.")
    else:
        print(
            f"\n  {Y}Partial{X} — branch created, "
            f"check workflow status manually."
        )


if __name__ == '__main__':
    main()
