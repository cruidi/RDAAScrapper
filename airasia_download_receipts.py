"""
AirAsia Receipt Downloader (Past Bookings - 2026)
=================================================
Logs into AirAsia, navigates to Past bookings (opens in new tab),
filters for 2026 flights, and downloads receipts for each unique booking.

The PDF receipt opens at flights-mmb.airasia.com — we intercept that URL
and download it directly using the browser's cookies (authenticated session).

Requirements:
    py -m pip install playwright
    py -m playwright install chromium

Usage:
    py airasia_download_receipts.py
"""

import asyncio
from pathlib import Path
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

# ── Configuration ─────────────────────────────────────────────────────────────

AIRASIA_EMAIL    = "cruidi@hotmail.co.uk"
AIRASIA_PASSWORD = "password"

DOWNLOAD_DIR = Path("./airasia_receipts_2026")
HEADLESS = False  # Keep False so you can complete 2FA manually

# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    print(f"  ➜  {msg}")


async def login_flow(page):
    log("Opening AirAsia homepage...")
    await page.goto("https://www.airasia.com/en/gb", wait_until="domcontentloaded")
    await page.wait_for_timeout(3000)

    log("Clicking Log in/Sign up...")
    login_btn = page.locator("p.Text__TextContainer-sc-xqubq8-0.Login__CustomText-sc-vur044-4").first
    try:
        await login_btn.wait_for(state="visible", timeout=10000)
        await login_btn.click()
    except PlaywrightTimeoutError:
        try:
            await page.locator("text=Log in/Sign up").first.click()
        except Exception:
            log("Could not find login button.")
            await page.screenshot(path=str(DOWNLOAD_DIR / "DEBUG_no_login_btn.png"))

    await page.wait_for_timeout(2000)

    log("Filling email...")
    email_input = page.locator("#text-input--login")
    await email_input.wait_for(state="visible", timeout=15000)
    await email_input.fill(AIRASIA_EMAIL)

    log("Filling password...")
    pw_input = page.locator("#password-input--login")
    await pw_input.wait_for(state="visible", timeout=10000)
    await pw_input.fill(AIRASIA_PASSWORD)

    log("Clicking Log in button...")
    await page.locator("#loginbutton").click()
    await page.wait_for_timeout(2000)

    # 2FA
    twofa_input = page.locator("#text-input--secondFA")
    try:
        await twofa_input.wait_for(state="visible", timeout=10000)
        log("=" * 55)
        log("🔐 2FA REQUIRED!")
        log("   Please enter your 2FA code in the browser window.")
        log("   Script will wait up to 2 minutes...")
        log("=" * 55)
        await page.wait_for_function(
            "() => !document.querySelector('#text-input--secondFA')",
            timeout=120000
        )
        log("2FA submitted. Waiting for page to settle...")
        await page.wait_for_timeout(3000)
    except PlaywrightTimeoutError:
        log("No 2FA prompt — continuing...")

    log("Verifying login...")
    try:
        await page.locator("#mybookings-universalHeader-linkContainer").wait_for(state="visible", timeout=20000)
        log("✅ Logged in successfully!")
    except PlaywrightTimeoutError:
        log("⚠️  Could not confirm login.")
        await page.screenshot(path=str(DOWNLOAD_DIR / "DEBUG_login_state.png"))


async def go_to_past_bookings(main_page, context):
    """Click Purchases (new tab opens), switch to it, click Past."""
    log("Clicking Purchases (opens new tab)...")
    purchases_link = main_page.locator("#mybookings-universalHeader-linkContainer")
    try:
        await purchases_link.wait_for(state="visible", timeout=10000)
        async with context.expect_page() as new_page_info:
            await purchases_link.click()
        orders_page = await new_page_info.value
    except PlaywrightTimeoutError:
        log("Header link not found — navigating directly...")
        orders_page = await context.new_page()
        await orders_page.goto("https://www.airasia.com/myorders/en/gb", wait_until="domcontentloaded")

    await orders_page.wait_for_load_state("domcontentloaded")
    await orders_page.wait_for_timeout(3000)
    log(f"Orders page: {orders_page.url}")

    await _click_past_tab(orders_page)
    return orders_page


async def _click_past_tab(orders_page):
    log("Clicking 'Past' tab...")
    past_chip = orders_page.locator("#chipText:has-text('Past')").first
    await past_chip.wait_for(state="visible", timeout=10000)
    await past_chip.click()
    await orders_page.wait_for_timeout(3000)
    log("Now on Past bookings.")


async def extract_2026_bookings(orders_page) -> list[tuple[str, str]]:
    log("Scanning table for 2026 bookings...")
    rows = await orders_page.locator("tr.Table__RowWrapperAction-sc-1kbzrgp-5").all()
    log(f"Total rows visible: {len(rows)}")

    seen = set()
    results = []
    for row in rows:
        try:
            date_text    = (await row.locator("td[id='date']").inner_text()).strip()
            booking_text = (await row.locator("td[id='bookingNumber']").inner_text()).strip()
            if "2026" in date_text and booking_text and booking_text not in seen:
                seen.add(booking_text)
                results.append((booking_text, date_text))
                log(f"   ✔ {booking_text}  —  {date_text}")
        except Exception:
            continue

    log(f"Unique 2026 bookings found: {len(results)}")
    return results


async def download_receipt(orders_page, context, booking_number: str, date_label: str, download_dir: Path) -> bool:
    """
    Click booking row → new detail tab opens.
    Click Download receipt → PDF opens at flights-mmb.airasia.com.
    Intercept that new PDF tab's URL and download via fetch() using session cookies.
    """
    # Refresh back to past bookings
    await orders_page.goto("https://www.airasia.com/myorders/en/gb", wait_until="domcontentloaded")
    await orders_page.wait_for_timeout(2000)
    await _click_past_tab(orders_page)

    # Find booking row
    booking_cell = orders_page.locator(f"td[id='bookingNumber']:has-text('{booking_number}')").first
    try:
        await booking_cell.wait_for(state="visible", timeout=10000)
    except PlaywrightTimeoutError:
        log(f"[{booking_number}] ⚠️  Row not visible.")
        return False

    # Click row → new detail tab
    log(f"[{booking_number}] Opening detail tab...")
    async with context.expect_page() as detail_info:
        await booking_cell.click()
    detail_page = await detail_info.value
    await detail_page.wait_for_load_state("domcontentloaded")
    await detail_page.wait_for_timeout(4000)
    log(f"[{booking_number}] Detail URL: {detail_page.url}")

    # Find Download receipt button
    receipt_btn = detail_page.locator("p.Text__TextContainer-sc-xqubq8-0:has-text('Download receipt')").first
    try:
        await receipt_btn.wait_for(state="visible", timeout=20000)
    except PlaywrightTimeoutError:
        log(f"[{booking_number}] ⚠️  'Download receipt' button not found.")
        await detail_page.screenshot(path=str(download_dir / f"DEBUG_{booking_number}.png"))
        await detail_page.close()
        return False

    # Click → PDF opens in a new tab at flights-mmb.airasia.com
    log(f"[{booking_number}] Clicking 'Download receipt' — waiting for PDF tab...")
    async with context.expect_page() as pdf_info:
        await receipt_btn.click()

    pdf_page = await pdf_info.value
    await pdf_page.wait_for_load_state("domcontentloaded")
    await pdf_page.wait_for_timeout(2000)

    pdf_url = pdf_page.url
    log(f"[{booking_number}] PDF URL: {pdf_url[:80]}...")

    # Use Playwright's evaluate + fetch to download the PDF using the active session cookies
    safe_date = date_label.replace(",", "").replace(" ", "_")
    save_path = download_dir / f"Receipt_{booking_number}_{safe_date}.pdf"

    try:
        # Fetch the PDF as base64 from within the browser context (carries cookies automatically)
        pdf_base64 = await pdf_page.evaluate("""
            async (url) => {
                const response = await fetch(url, { credentials: 'include' });
                const buffer = await response.arrayBuffer();
                const bytes = new Uint8Array(buffer);
                let binary = '';
                for (let i = 0; i < bytes.byteLength; i++) {
                    binary += String.fromCharCode(bytes[i]);
                }
                return btoa(binary);
            }
        """, pdf_url)

        import base64
        pdf_bytes = base64.b64decode(pdf_base64)
        save_path.write_bytes(pdf_bytes)
        log(f"[{booking_number}] ✅ Saved: {save_path.name}  ({len(pdf_bytes):,} bytes)")
        await pdf_page.close()
        await detail_page.close()
        return True

    except Exception as e:
        log(f"[{booking_number}] ❌ Fetch failed: {e}")
        # Fallback: press Ctrl+S on the PDF page to trigger Save As
        log(f"[{booking_number}] Trying Ctrl+S fallback (saves to default Downloads folder)...")
        try:
            await pdf_page.bring_to_front()
            await pdf_page.keyboard.press("Control+s")
            await pdf_page.wait_for_timeout(3000)
            log(f"[{booking_number}] ✅ Ctrl+S sent — file saved to your Downloads folder.")
            await pdf_page.close()
            await detail_page.close()
            return True
        except Exception as e2:
            log(f"[{booking_number}] ❌ Ctrl+S also failed: {e2}")
            await pdf_page.screenshot(path=str(download_dir / f"DEBUG_{booking_number}.png"))
            await pdf_page.close()
            await detail_page.close()
            return False


# ── Main ──────────────────────────────────────────────────────────────────────

async def main():
    DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
    print()
    print("🛫  AirAsia Receipt Downloader — Past Bookings 2026")
    print("=" * 55)

    results = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(accept_downloads=True)
        main_page = await context.new_page()

        # 1. Login
        await login_flow(main_page)

        # 2. Click Purchases → new tab → Past
        orders_page = await go_to_past_bookings(main_page, context)

        # 3. Extract 2026 bookings
        bookings = await extract_2026_bookings(orders_page)

        if not bookings:
            log("No 2026 bookings found.")
            await orders_page.screenshot(path=str(DOWNLOAD_DIR / "DEBUG_empty.png"))
        else:
            for i, (booking_number, date_label) in enumerate(bookings, 1):
                print(f"\n[{i}/{len(bookings)}] {booking_number}  ({date_label})")
                try:
                    success = await download_receipt(
                        orders_page, context, booking_number, date_label, DOWNLOAD_DIR
                    )
                    results[booking_number] = f"✅ OK     ({date_label})" if success else f"⚠️  Skip  ({date_label})"
                except Exception as e:
                    results[booking_number] = f"❌ Error  ({e})"
                    log(f"Unexpected error: {e}")

        await browser.close()

    print()
    print("📋  SUMMARY")
    print("=" * 55)
    for bnum, status in results.items():
        print(f"  {bnum:<12}  {status}")
    total_ok = sum(1 for s in results.values() if s.startswith("✅"))
    print(f"\n  Downloaded: {total_ok} / {len(results)}")
    print(f"  Folder: {DOWNLOAD_DIR.resolve()}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
