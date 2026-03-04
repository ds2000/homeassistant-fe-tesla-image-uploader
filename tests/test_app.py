"""
Smoke tests for the Tesla Card Image Uploader web app.
Tests navigation, step transitions, back/start-over flows, and data loading.

Usage:
    python3 tests/test_app.py [URL]

Default URL: https://homeassistant-fe-tesla-image-uploader.pages.dev/
"""

import sys
import time

from playwright.sync_api import sync_playwright, expect

BASE_URL = sys.argv[1] if len(sys.argv) > 1 else "https://homeassistant-fe-tesla-image-uploader.pages.dev/"

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
results = []


def report(name, passed, detail=""):
    tag = PASS if passed else FAIL
    msg = f"  {tag}  {name}"
    if detail:
        msg += f"  ({detail})"
    print(msg)
    results.append((name, passed))


def run_tests():
    with sync_playwright() as p:
        browser = p.chromium.launch()
        context = browser.new_context(viewport={"width": 480, "height": 900})
        page = context.new_page()

        console_errors = []
        page.on("console", lambda msg: console_errors.append(msg.text) if msg.type == "error" else None)

        # ─── 1. Page loads without JS errors ────────────────────────────
        print("\n── Page load ──")
        page.goto(BASE_URL, wait_until="networkidle")
        page.wait_for_timeout(2000)

        report("Page loads", page.title() != "")
        js_errors = [e for e in console_errors if "SES Removing" not in e]
        report("No JS errors on load", len(js_errors) == 0,
               f"{len(js_errors)} error(s)" if js_errors else "")
        for e in js_errors:
            print(f"        → {e}")

        # ─── 2. Step 1 visible, steps 2 & 3 hidden ─────────────────────
        print("\n── Step 1: Select ──")
        step1 = page.locator("#step-1")
        step2 = page.locator("#step-2")
        step3 = page.locator("#step-3")

        report("Step 1 visible", step1.is_visible())
        report("Step 2 hidden", not step2.is_visible())
        report("Step 3 hidden", not step3.is_visible())

        # Stepper shows step 1 active
        stepper1 = page.locator('.stepper-step[data-step="1"]')
        report("Stepper 1 is active", "active" in stepper1.get_attribute("class"))

        # ─── 3. models.json loads, dropdowns populate ───────────────────
        print("\n── Data loading ──")
        model_select = page.locator("#select-model")
        report("Model dropdown enabled", not model_select.is_disabled(),
               model_select.inner_text()[:50])

        # Count model options (exclude placeholder)
        model_options = page.locator("#select-model option:not([value=''])")
        n_models = model_options.count()
        report("Models loaded", n_models > 0, f"{n_models} model(s)")

        # ─── 4. Select a model → variants populate ─────────────────────
        print("\n── Dropdown cascade ──")
        # Pick first real model
        first_model_val = model_options.nth(0).get_attribute("value")
        model_select.select_option(first_model_val)
        page.wait_for_timeout(300)

        variant_select = page.locator("#select-variant")
        report("Variant dropdown enabled", not variant_select.is_disabled())
        variant_options = page.locator("#select-variant option:not([value=''])")
        n_variants = variant_options.count()
        report("Variants loaded", n_variants > 0, f"{n_variants} variant(s)")

        # Pick first variant → colours populate
        first_variant_val = variant_options.nth(0).get_attribute("value")
        variant_select.select_option(first_variant_val)
        page.wait_for_timeout(300)

        colour_select = page.locator("#select-colour")
        report("Colour dropdown enabled", not colour_select.is_disabled())
        colour_options = page.locator("#select-colour option:not([value=''])")
        n_colours = colour_options.count()
        report("Colours loaded", n_colours > 0, f"{n_colours} colour(s)")

        # Pick first available (enabled) colour
        first_colour_val = None
        for i in range(n_colours):
            opt = colour_options.nth(i)
            if not opt.is_disabled():
                first_colour_val = opt.get_attribute("value")
                break
        if first_colour_val is None:
            # All colours disabled — pick the first one anyway via JS to continue testing
            first_colour_val = colour_options.nth(0).get_attribute("value")
            page.evaluate(f'document.getElementById("select-colour").value = "{first_colour_val}"; '
                          f'document.getElementById("select-colour").dispatchEvent(new Event("change"))')
            report("Colour selected (all disabled, forced)", True, "no available colours")
        else:
            colour_select.select_option(first_colour_val)
            report("Colour selected (available)", True, first_colour_val)
        page.wait_for_timeout(300)

        # Continue button should be enabled
        btn_continue = page.locator("#btn-to-step2")
        report("Continue button enabled", not btn_continue.is_disabled())

        # ─── 5. Click Continue → Step 2 ────────────────────────────────
        print("\n── Step 1 → Step 2 ──")
        btn_continue.click()
        page.wait_for_timeout(500)

        report("Step 2 visible", step2.is_visible())
        report("Step 1 hidden", not step1.is_visible())
        stepper2 = page.locator('.stepper-step[data-step="2"]')
        report("Stepper 2 is active", "active" in stepper2.get_attribute("class"))

        # Combo label populated
        combo_label = page.locator("#verify-combo-label")
        report("Combo label shows selection", len(combo_label.inner_text()) > 0,
               combo_label.inner_text())

        # Email input present and send button disabled
        email_input = page.locator("#input-email")
        btn_send = page.locator("#btn-send-verify")
        report("Email input visible", email_input.is_visible())
        report("Send button disabled (no email)", btn_send.is_disabled())

        # Type email → send button enables
        email_input.fill("test@example.com")
        page.wait_for_timeout(200)
        report("Send button enabled (with email)", not btn_send.is_disabled())

        # ─── 6. Back button → Step 1 ───────────────────────────────────
        print("\n── Step 2 → Back → Step 1 ──")
        btn_back = page.locator("#btn-back-to-step1")
        btn_back.click()
        page.wait_for_timeout(500)

        report("Step 1 visible after back", step1.is_visible())
        report("Step 2 hidden after back", not step2.is_visible())
        report("Stepper 1 active after back", "active" in stepper1.get_attribute("class"))

        # Dropdowns should still have selections
        report("Model selection preserved", model_select.input_value() == first_model_val)
        report("Variant selection preserved", variant_select.input_value() == first_variant_val)
        report("Colour selection preserved", colour_select.input_value() == first_colour_val)
        report("Continue still enabled", not btn_continue.is_disabled())

        # ─── 7. Go to Step 2 again, skip verification → Step 3 ─────────
        print("\n── Step 2 → Dev Skip → Step 3 ──")
        btn_continue.click()
        page.wait_for_timeout(500)

        btn_dev_skip = page.locator("#btn-dev-skip")
        dev_skip_exists = btn_dev_skip.count() > 0 and btn_dev_skip.is_visible()
        if not dev_skip_exists:
            report("Dev skip hidden on production", True)
        if dev_skip_exists:
            btn_dev_skip.click()
            page.wait_for_timeout(500)

            report("Step 3 visible", step3.is_visible())
            report("Step 2 hidden", not step2.is_visible())
            stepper3 = page.locator('.stepper-step[data-step="3"]')
            report("Stepper 3 is active", "active" in stepper3.get_attribute("class"))

            # Upload wizard loaded
            sidebar_items = page.locator("#upload-sidebar-list li")
            n_items = sidebar_items.count()
            report("Upload sidebar populated", n_items > 0, f"{n_items} items")
            # 15 layers + 2 section headers (Unplugged, On charge) = 17
            report("Upload sidebar has 15 layers + headers", n_items == 17,
                   f"expected 17, got {n_items}")

            # Counter shows 0 of 15
            counter = page.locator("#upload-counter")
            report("Counter shows 0 of 15", "0 of 15" in counter.inner_text(),
                   counter.inner_text())

            # Upload combo label populated
            upload_combo = page.locator("#upload-combo-label")
            report("Upload combo label set", len(upload_combo.inner_text()) > 0,
                   upload_combo.inner_text())

            # Nav indicator shows 1 / 15
            nav_ind = page.locator("#upload-nav-indicator")
            report("Nav indicator shows 1 / 15", "1 / 15" in nav_ind.inner_text())

            # Guide image area present
            guide = page.locator("#upload-guide")
            report("Guide area visible", guide.is_visible())

            # Instructions area present
            instructions = page.locator("#upload-instructions")
            report("Instructions area visible", instructions.is_visible())

            # ─── 8. Navigate upload wizard with Next/Back ────────────
            print("\n── Upload wizard navigation ──")
            btn_next = page.locator("#btn-upload-next")
            btn_prev = page.locator("#btn-upload-prev")

            btn_next.click()
            page.wait_for_timeout(300)
            report("Next → layer 2", "2 / 15" in nav_ind.inner_text())

            btn_next.click()
            page.wait_for_timeout(300)
            report("Next → layer 3", "3 / 15" in nav_ind.inner_text())

            btn_prev.click()
            page.wait_for_timeout(300)
            report("Prev → layer 2", "2 / 15" in nav_ind.inner_text())

            btn_prev.click()
            page.wait_for_timeout(300)
            report("Prev → layer 1", "1 / 15" in nav_ind.inner_text())

            # Click sidebar item directly (skip section headers — they're not clickable layers)
            # Layer items have a data-index attribute; section headers don't
            layer_items = page.locator("#upload-sidebar-list li[data-index]")
            n_layers = layer_items.count()
            if n_layers >= 5:
                layer_items.nth(4).click()
                page.wait_for_timeout(300)
                report("Sidebar click → layer 5", "5 / 15" in nav_ind.inner_text(),
                       nav_ind.inner_text())

            # ─── 9. Start over → back to Step 1 ─────────────────────
            print("\n── Start over → Step 1 ──")
            btn_start_over = page.locator("#btn-back-to-step1-from3")
            btn_start_over.click()
            page.wait_for_timeout(500)

            report("Step 1 visible after start over", step1.is_visible())
            report("Step 3 hidden after start over", not step3.is_visible())
            report("Stepper 1 active after start over", "active" in stepper1.get_attribute("class"))

            # Dropdowns should still work
            report("Model dropdown still enabled", not model_select.is_disabled())

            # ─── 10. Full round-trip: select again → step 2 → back ──
            print("\n── Full round-trip after start over ──")
            model_select.select_option(first_model_val)
            page.wait_for_timeout(200)
            variant_select.select_option(first_variant_val)
            page.wait_for_timeout(200)
            colour_select.select_option(first_colour_val)
            page.wait_for_timeout(200)
            btn_continue.click()
            page.wait_for_timeout(500)

            report("Step 2 visible on re-entry", step2.is_visible())
            report("Combo label correct on re-entry",
                   len(combo_label.inner_text()) > 0, combo_label.inner_text())

            btn_back.click()
            page.wait_for_timeout(500)
            report("Back to step 1 works on re-entry", step1.is_visible())

        else:
            report("Dev skip button visible", False, "button not found — skipping step 3 tests")

        # ─── 11. Session storage cleared after start over ────────────
        print("\n── Session storage ──")
        verified = page.evaluate("sessionStorage.getItem('verified')")
        report("Session 'verified' cleared after start over", verified is None)

        # ─── 12. Page reload on Step 1 ──────────────────────────────
        print("\n── Page reload ──")
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(2000)

        report("Step 1 visible after reload", page.locator("#step-1").is_visible())
        report("Model dropdown re-populated after reload",
               not page.locator("#select-model").is_disabled())

        reload_errors = [e for e in console_errors if "SES Removing" not in e]
        report("No JS errors after reload", len(reload_errors) == 0)

        # ─── Summary ────────────────────────────────────────────────
        browser.close()

    passed = sum(1 for _, p in results if p)
    failed = sum(1 for _, p in results if not p)
    total = len(results)

    print(f"\n{'='*50}")
    print(f"  {passed}/{total} passed", end="")
    if failed:
        print(f", {FAIL} {failed} failed")
        print()
        for name, p in results:
            if not p:
                print(f"    ✗ {name}")
    else:
        print(f"  — all {PASS}")
    print()

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
