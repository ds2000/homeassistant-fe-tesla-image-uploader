#!/usr/bin/env python3
"""
Card Button → Service Call Verification

Static analysis of tesla-card source files to verify every interactive
button calls the correct Home Assistant service with the right entity.

Usage:
    python3 tests/test_card_buttons.py
    python3 tests/test_card_buttons.py /path/to/card/src
"""

import re
import sys
from pathlib import Path

# ── Config ──────────────────────────────────────────────────────────────

DEFAULT_CARD_SRC = (
    Path(__file__).resolve().parent.parent.parent
    / 'homeassistant-fe-tesla' / 'src'
)

SOURCE_FILES = [
    'menu-controls.js',
    'menu-climate.js',
    'menu-charger.js',
    'tesla-card.js',
]

# Ground truth: expected (domain, service, entity_key) per file.
# Ternary patterns are expanded into both branches.
EXPECTED_CALLS = {
    'menu-controls.js': [
        ('cover',  'toggle_cover', 'FRUNK_COVER'),
        ('lock',   'unlock',       'DOOR_LOCK'),
        ('lock',   'lock',         'DOOR_LOCK'),
        ('button', 'press',        'OPEN_TRUNK'),
        ('button', 'press',        'CHARGE_PORT_OPEN'),
        ('button', 'press',        'CHARGE_PORT_CLOSE'),
        ('button', 'press',        'FLASH_LIGHTS'),
        ('button', 'press',        'HORN'),
        ('button', 'press',        'REMOTE_START'),
        ('cover',  'close_cover',  'WINDOWS_COVER'),
        ('cover',  'open_cover',   'WINDOWS_COVER'),
    ],
    'menu-climate.js': [
        ('climate', 'set_temperature', 'CLIMATE'),
        ('select',  'select_next',     'HEATED_SEAT_LEFT'),
        ('select',  'select_next',     'HEATED_SEAT_RIGHT'),
        ('select',  'select_next',     'HEATED_SEAT_REAR_LEFT'),
        ('select',  'select_next',     'HEATED_SEAT_REAR_CENTER'),
        ('select',  'select_next',     'HEATED_SEAT_REAR_RIGHT'),
        ('climate', 'turn_off',        'CLIMATE'),
        ('climate', 'turn_on',         'CLIMATE'),
        ('cover',   'close_cover',     'WINDOWS_COVER'),
        ('cover',   'open_cover',      'WINDOWS_COVER'),
        ('switch',  'turn_off',        'DEFROST_SWITCH'),
        ('switch',  'turn_on',         'DEFROST_SWITCH'),
        ('switch',  'turn_off',        'CAMP_MODE'),
        ('switch',  'turn_on',         'CAMP_MODE'),
        ('switch',  'turn_off',        'DOG_MODE'),
        ('switch',  'turn_on',         'DOG_MODE'),
        ('select',  'select_option',   'CABIN_OVERHEAT'),
    ],
    'menu-charger.js': [
        ('number', 'set_value', 'CHARGE_LIMIT_NUMBER'),
        ('number', 'set_value', 'CHARGING_AMPS_NUMBER'),
        ('button', 'press',     'CHARGE_PORT_OPEN'),
        ('button', 'press',     'CHARGE_PORT_CLOSE'),
    ],
    'tesla-card.js': [
        ('button', 'press',  'FORCE_UPDATE'),
        ('lock',   'unlock', 'DOOR_LOCK'),
        ('lock',   'lock',   'DOOR_LOCK'),
    ],
}

# Valid HA services per domain
VALID_SERVICES = {
    'button':  {'press'},
    'lock':    {'lock', 'unlock'},
    'cover':   {'open_cover', 'close_cover', 'toggle_cover', 'stop_cover'},
    'switch':  {'turn_on', 'turn_off', 'toggle'},
    'climate': {'turn_on', 'turn_off', 'set_temperature', 'set_hvac_mode',
                'set_fan_mode', 'set_preset_mode'},
    'select':  {'select_option', 'select_next', 'select_previous'},
    'number':  {'set_value'},
}

# ── ANSI colours ────────────────────────────────────────────────────────

G = '\033[92m'
R = '\033[91m'
Y = '\033[93m'
B = '\033[1m'
X = '\033[0m'

# ── Parsing ─────────────────────────────────────────────────────────────


def parse_entities(filepath):
    """Parse ENTITIES object from entity-config.js → {KEY: template}."""
    content = filepath.read_text()
    entities = {}
    pattern = re.compile(r"(\w+):\s*'([^']+)'")
    in_block = False
    brace_depth = 0
    for line in content.splitlines():
        if not in_block:
            if re.search(r'\bENTITIES\b.*\{', line):
                in_block = True
                brace_depth = line.count('{') - line.count('}')
                continue
        else:
            brace_depth += line.count('{') - line.count('}')
            if brace_depth <= 0:
                break
            m = pattern.search(line)
            if m:
                entities[m.group(1)] = m.group(2)
    return entities


def extract_svc_calls(filepath):
    """Extract all _svc() calls → list of (domain, service, entity_key).

    Handles three patterns:
      1. Simple:         _svc('domain', 'service', ENTITIES.KEY)
      2. Ternary service: _svc('domain', cond ? 'svc_a' : 'svc_b', ENTITIES.KEY)
      3. Ternary entity:  _svc('domain', 'service', cond ? ENTITIES.A : ENTITIES.B)
    """
    content = filepath.read_text()
    calls = []

    for line in content.splitlines():
        stripped = line.lstrip()
        if stripped.startswith('//') or stripped.startswith('/*'):
            continue
        if '_svc(' not in line:
            continue

        m = re.search(r'_svc\((.+)\)', line)
        if not m:
            continue
        args = m.group(1)

        # Domain is always the first quoted string
        dm = re.match(r"\s*'(\w+)'", args)
        if not dm:
            continue
        domain = dm.group(1)

        # Detect ternary patterns
        ternary_svc = re.search(r"\?\s*'(\w+)'\s*:\s*'(\w+)'", args)
        ternary_ent = re.search(
            r"\?\s*ENTITIES\.(\w+)\s*:\s*ENTITIES\.(\w+)", args
        )

        # Simple service: first 'word' after a comma
        simple_svc = re.search(r",\s*'(\w+)'", args)

        # Entity references — only look before any { (extra data params)
        main_args = args.split('{')[0] if '{' in args else args
        all_ents = re.findall(r"ENTITIES\.(\w+)", main_args)

        if ternary_svc and not ternary_ent:
            # Ternary service, simple entity
            svc_a, svc_b = ternary_svc.group(1), ternary_svc.group(2)
            ent = all_ents[-1] if all_ents else None
            if ent:
                calls.append((domain, svc_a, ent))
                calls.append((domain, svc_b, ent))

        elif ternary_ent and not ternary_svc:
            # Simple service, ternary entity
            svc = simple_svc.group(1) if simple_svc else None
            ent_a, ent_b = ternary_ent.group(1), ternary_ent.group(2)
            if svc:
                calls.append((domain, svc, ent_a))
                calls.append((domain, svc, ent_b))

        elif all_ents and simple_svc:
            # Simple service, simple entity
            calls.append((domain, simple_svc.group(1), all_ents[-1]))

    return calls


def entity_domain(template):
    """Extract domain prefix from entity template (e.g. 'lock.{car}' → 'lock')."""
    return template.split('.')[0] if '.' in template else None


# ── Main ────────────────────────────────────────────────────────────────


def main():
    card_src = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CARD_SRC
    if not card_src.is_dir():
        print(f"{R}ERROR{X}: Card source not found: {card_src}")
        sys.exit(1)

    print(f"\n{B}Card Button → Service Call Verification{X}")
    print(f"Source: {card_src}\n")

    entity_file = card_src / 'entity-config.js'
    if not entity_file.exists():
        print(f"{R}ERROR{X}: entity-config.js not found")
        sys.exit(1)

    entities = parse_entities(entity_file)
    print(f"Parsed {len(entities)} entity definitions\n")

    passed = failed = warnings = 0

    for filename in SOURCE_FILES:
        filepath = card_src / filename
        if not filepath.exists():
            print(f"  {R}FAIL{X}: {filename} not found")
            failed += 1
            continue

        print(f"{B}── {filename} ──{X}")

        actual = extract_svc_calls(filepath)
        expected = EXPECTED_CALLS.get(filename, [])

        actual_set = set(actual)
        expected_set = set(expected)

        # ── Domain consistency + valid services ──

        for domain, service, ent_key in sorted(actual_set):
            if ent_key not in entities:
                print(
                    f"  {R}FAIL{X}: ENTITIES.{ent_key} not defined "
                    f"in entity-config.js"
                )
                failed += 1
                continue

            tmpl = entities[ent_key]
            ed = entity_domain(tmpl)
            if ed != domain:
                print(
                    f"  {R}FAIL{X}: domain mismatch — "
                    f"_svc('{domain}', ..., ENTITIES.{ent_key}) "
                    f"but template '{tmpl}' → domain '{ed}'"
                )
                failed += 1
                continue

            valid = VALID_SERVICES.get(domain, set())
            if service not in valid:
                print(
                    f"  {R}FAIL{X}: '{service}' not valid for "
                    f"domain '{domain}' (valid: {valid})"
                )
                failed += 1
                continue

        # ── Missing expected calls ──

        for call in sorted(expected_set - actual_set):
            print(
                f"  {R}MISSING{X}: expected "
                f"{call[0]}.{call[1]} → ENTITIES.{call[2]}"
            )
            failed += 1

        # ── Unexpected actual calls ──

        for call in sorted(actual_set - expected_set):
            print(
                f"  {Y}NEW{X}: {call[0]}.{call[1]} → ENTITIES.{call[2]} "
                f"(not in expected list)"
            )
            warnings += 1

        # ── Matched calls ──

        for call in sorted(actual_set & expected_set):
            print(f"  {G}PASS{X}: {call[0]}.{call[1]} → ENTITIES.{call[2]}")
            passed += 1

        print()

    # ── Summary ──

    print(f"{B}{'─' * 50}{X}")
    parts = [f"{G}{passed} passed{X}"]
    if failed:
        parts.insert(0, f"{R}{failed} FAILED{X}")
    if warnings:
        parts.append(f"{Y}{warnings} new calls{X}")
    print(' / '.join(parts))

    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
