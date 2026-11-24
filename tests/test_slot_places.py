"""E: every slot in the contract shows on a screen the user can reach.

Static part: each slot in coreapi.SLOTS has a container and a render call on a named screen. A new slot
added to the contract without a place fails here. Browser part (when Chrome and Playwright are present):
panels in the home, header and settings slots are visible to a user.
"""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path

from engine import coreapi, features, userspace

ROOT = Path(__file__).resolve().parent.parent
HTML = (ROOT / "web" / "index.html").read_text()

# slot -> (where the user sees it, text that must be in the page code)
PLACES = {
    "home": ("Home page (side bar: Home; first screen)", ['id="slotHome"', 'renderSlot($("#slotHome"), "home"']),
    "header": ("Top bar, every screen", ['id="slotHeader"', 'renderSlot($("#slotHeader"), "header"']),
    "settings": ("Defaults sheet", ['id="slotSettings"', 'renderSlot($("#slotSettings"), "settings"']),
}


class SlotPlaceTests(unittest.TestCase):
    def test_every_contract_slot_has_a_place(self):
        missing = [s for s in coreapi.SLOTS if s not in PLACES]
        self.assertEqual(missing, [], "Add a place on a screen for these slots before they go in the contract.")

    def test_each_place_is_in_the_page_code(self):
        code = HTML
        for slot, (_, needles) in PLACES.items():
            for n in needles:
                self.assertTrue(n in code, "slot %s: %s is not in the page code" % (slot, n))

    def test_the_home_slot_is_on_the_home_page_not_the_new_task_screen(self):
        start = HTML[HTML.index("async function showStart("):HTML.index("async function showHome()")]
        self.assertTrue("slotHome" not in start, "the home slot is on the New task screen")


def _browser():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return None
    from engine import vshots
    return vshots.find_chrome()


@unittest.skipUnless(_browser(), "Chrome and Playwright are not installed")
class SlotBrowserTests(unittest.TestCase):
    def test_panels_in_home_header_and_settings_are_visible(self):
        import asyncio
        from playwright.async_api import async_playwright
        from engine import config
        from engine.server import Engine, make_server
        from unittest import mock
        home = Path(tempfile.mkdtemp()) / "Duo"
        userspace.ensure(home)
        d = userspace.user_dir(home) / "features" / "marks"
        (d / "ui").mkdir(parents=True)
        (d / "__init__.py").write_text("")
        (d / "feature.json").write_text(json.dumps({"name": "marks", "contract": ">=1.2"}))
        (d / "feature.py").write_text("def register(api):\n" + "".join(
            "    api.slot('%s', 'ui/%s.js')\n" % (s, s.replace(".", "_")) for s in ("home", "header", "settings")))
        for s in ("home", "header", "settings"):
            (d / "ui" / (s + ".js")).write_text("export default (el) => { el.textContent = 'MARK-%s'; };" % s)
        prev = features.active()
        with mock.patch.object(config, "DUO_HOME", home):
            srv = make_server(0, Engine(agents=[], tick=False, home=home))
            port = srv.server_address[1]
            threading.Thread(target=srv.serve_forever, daemon=True).start()

            async def go():
                async with async_playwright() as p:
                    b = await p.chromium.launch(executable_path=_browser(), args=["--no-sandbox"])
                    pg = await b.new_page()
                    await pg.goto("http://127.0.0.1:%d/?snapshot=1#/home" % port)
                    await pg.wait_for_timeout(1500)
                    seen = {s: await pg.locator("text=MARK-%s" % s).is_visible() for s in ("home", "header")}
                    await pg.click("text=Defaults")
                    await pg.wait_for_timeout(800)
                    seen["settings"] = await pg.locator("text=MARK-settings").is_visible()
                    await b.close()
                    return seen
            try:
                self.assertEqual(asyncio.run(go()), {"home": True, "header": True, "settings": True})
            finally:
                srv.shutdown()
                features.activate(prev)


if __name__ == "__main__":
    unittest.main()
