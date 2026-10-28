"""End-to-end desktop test against the bundled runtime, with synthetic audio.

Usage: .venv/bin/python tests/desktop_smoke.py --resources dist/TuneSaver.app/Contents/Resources
No platform account, external service, or system Keychain entry is used.
"""
import argparse
import asyncio
import json
import math
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]


def server(args):
    sys.path.insert(0, str(args.resources / "src"))
    import desktop

    class MemoryVault:
        def get(self, provider): return {}
        def set(self, provider, value): pass
        def delete(self, provider): pass

    original = desktop.Desktop.register
    def register(self, app):
        original(self, app)
        api, music = self.api, self.music
        if api.local_user_history(desktop.LOCAL_USER_ID):
            return
        job_id = music.enqueue("tiddl", ["track/123"], owner_id=desktop.LOCAL_USER_ID,
            ephemeral=True, metadata={"title": "Essai local", "artist": "Audio de test", "kind": "track"})
        source = music.QUEUE_ROOT / "pending/tiddl" / (job_id + ".json")
        job = music.load_job(source)
        folder = music.DELIVERY_ROOT / desktop.LOCAL_USER_ID / job_id / "content"
        folder.mkdir(parents=True)
        with wave.open(str(folder / "Essai local.wav"), "wb") as audio:
            rate = 22050
            audio.setparams((1, 2, rate, 0, "NONE", "not compressed"))
            audio.writeframes(b"".join(struct.pack("<h", int(2000 * math.sin(i * 2 * math.pi * 440 / rate))) for i in range(rate * 8)))
        job["last_finished_at"] = "2020-01-01T00:00:00+00:00"
        job["exit_code"] = 0
        music.prepare_delivery(job, folder)
        music.atomic_write_json(music.QUEUE_ROOT / "done/tiddl" / source.name, job)
        source.unlink()
        import audio_retention
        api.local_upsert_history(desktop.LOCAL_USER_ID, [audio_retention.history(job)])

    desktop.KeychainVault = MemoryVault
    desktop.Desktop.register = register
    sys.argv = ["desktop.py", "--resources", str(args.resources), "--data-dir", args.data_dir, "--parent-watch"]
    desktop.main()


async def browser_test(args):
    from playwright.async_api import async_playwright, expect
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(ROOT / ".build/browsers")
    with tempfile.TemporaryDirectory(prefix="tunesaver-smoke-") as folder:
        async def start():
            process = await asyncio.create_subprocess_exec(str(args.resources / "python/bin/python3.13"),
                str(Path(__file__).resolve()), "--server", "--resources", str(args.resources), "--data-dir", folder,
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, cwd="/tmp")
            line = await asyncio.wait_for(process.stdout.readline(), 30)
            if not line.startswith(b"TUNESAVER_READY "):
                raise RuntimeError((await process.stderr.read()).decode())
            return process, line.decode().strip().split(" ", 1)[1]

        async def stop(process):
            process.stdin.close()
            await asyncio.wait_for(process.wait(), 20)
            assert process.returncode == 0, (await process.stderr.read()).decode()
            assert not (Path(folder) / "session").exists(), "Transient platform credentials were not cleaned"

        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            process, url = await start()
            try:
                context = await browser.new_context(viewport={"width": 1280, "height": 840}, accept_downloads=True)
                await context.route("**/*", lambda route: route.continue_() if route.request.url.startswith("http://127.0.0.1:") else route.abort())
                page = await context.new_page()
                errors = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                await page.goto(url)
                await expect(page.locator("#desktop-accounts")).to_be_visible()
                await expect(page.locator("#auth-view")).to_be_hidden()
                await expect(page.locator(".desktop-account-card")).to_have_count(3)
                await page.screenshot(path=str(ROOT / ".build/desktop-accounts.png"))
                await page.locator("#desktop-close").click()
                await expect(page.locator("#library-tracks")).to_contain_text("Essai local")
                await page.locator("#create-playlist").click()
                await page.locator("#action-dialog-input").fill("Mon set hors ligne")
                await page.locator("#action-dialog-submit").click()
                await expect(page.locator("#playlist-list")).to_contain_text("Mon set hors ligne")
                await page.evaluate("setWorkspaceView('library'); selectedPlaylistId = ''; refreshLibrary()")
                await page.locator(".library-track .play-button").first.click()
                await page.wait_for_function("() => document.querySelector('#player-audio').currentTime > 0.1")
                payload = await page.evaluate("requestJson('/api/library')")
                job = payload["tracks"][0]["job_id"]
                assert payload["tracks"][0]["streamable"] and not payload["tracks"][0]["expired"]
                await page.evaluate("job => requestJson('/api/mixer/tracks/' + job, {method:'POST', body:'{}'})", job)
                for _ in range(120):
                    analysis = await page.evaluate("job => requestJson('/api/mixer/tracks/' + job)", job)
                    if analysis['status'] in ('done', 'failed'):
                        break
                    await asyncio.sleep(.25)
                assert analysis['status'] == 'done', analysis
                assert abs(analysis['analysis']['duration'] - 8) < .1
                async with page.expect_download() as info:
                    await page.evaluate("job => downloadReadyJob(job)", job)
                download = await info.value
                target = Path(folder) / "export.wav"
                await download.save_as(str(target))
                assert target.read_bytes().startswith(b"RIFF")
                await page.screenshot(path=str(ROOT / ".build/desktop-library.png"))
                assert not errors, errors
                await context.close()
                await stop(process)
                process, url = await start()
                context = await browser.new_context()
                page = await context.new_page()
                await page.goto(url)
                await expect(page.locator("#desktop-accounts")).to_be_visible()
                await page.locator("#desktop-close").click()
                await expect(page.locator("#playlist-list")).to_contain_text("Mon set hors ligne")
                await expect(page.locator("#library-tracks")).to_contain_text("Essai local")
                await context.close()
                print("PASS: bundled runtime, onboarding, playlist creation, local playback, bundled audio analysis, WAV export, old audio retention, restart persistence, cleanup, no JavaScript errors")
            finally:
                if process.returncode is None:
                    await stop(process)
                await browser.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--resources", type=lambda value: Path(value).resolve(), required=True)
    parser.add_argument("--server", action="store_true")
    parser.add_argument("--data-dir")
    arguments = parser.parse_args()
    server(arguments) if arguments.server else asyncio.run(browser_test(arguments))
