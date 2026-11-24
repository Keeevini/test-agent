from pathlib import Path
import os
import sys
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "src" / "static" / "js"
FIXTURE = Path(__file__).with_suffix(".js")
# Only the feature's ES modules and its direct API dependency; no personal files.
names = ["api.js", *[f"conversation/{name}.js" for name in (
    "activity", "approval", "controller", "history", "markdown",
    "messages", "reasoning", "tool-cards",
)]]
sources = {name: (JS / name).read_text(encoding="utf-8") for name in names}

with sync_playwright() as p:
    browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_EXECUTABLE"))
    page = browser.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.route("**/*", lambda route: route.abort())
    page.set_content('<main id="messages"></main><button id="scroll"></button>')
    page.evaluate(r'''sources => {
      const urls = new Map();
      function urlFor(name) {
        if (urls.has(name)) return urls.get(name);
        if (!Object.hasOwn(sources, name)) throw new Error(`Unexpected module: ${name}`);
        // All existing feature imports are static relative imports.
        const code = sources[name].replace(/(\bfrom\s*|\bimport\s*)(['"])(\.[^'"]+)\2/g,
          (_, prefix, quote, relative) => {
            const dependency = new URL(relative, 'https://fixture.invalid/' + name).pathname.slice(1);
            return prefix + quote + urlFor(dependency) + quote;
          });
        const url = URL.createObjectURL(new Blob([code], {type:'text/javascript'}));
        urls.set(name, url);
        return url;
      }
      window.loadModule = name => import(urlFor(name));
    }''', sources)
    result = page.evaluate(FIXTURE.read_text(encoding="utf-8"), sys.argv[1])
    assert result is True, result
    assert not errors, errors
    print(f"PASS: {sys.argv[1]}")
    browser.close()
