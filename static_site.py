"""Shared publishing step for generated and archived static reports.

Run ``python static_site.py optimize`` after report generation. Stock data stays
beside each report and is fetched only when its chart is opened. No market data
is fetched or recalculated by this module.
"""

import argparse
import html
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil


SOURCE_ROOT = Path(__file__).resolve().parent
CHART_DECL = re.compile(r"const (stockCharts|chartsData)\s*=\s*")
SCRIPT = re.compile(r"<script\b([^>]*)>([\s\S]*?)</script>", re.I)
STATIC_VERSION = "3"
LEGACY_REPORTS = ('daily_first_j13_interactive.html', 'first_j13_step_daily_count.html',
                  'industry_total_amount_trend.html')


def public_pages(root):
    """Keep existing root report URLs while excluding source files and emails."""
    root = Path(root)
    legacy = [root / name for name in LEGACY_REPORTS if (root / name).is_file()]
    legacy += sorted(root.glob('stock_selection_????????.html'))
    return [root / 'index.html', *legacy, *sorted((root / 'html').rglob('*.html'))]


def finite_json(value):
    """JSON has no NaN/Infinity; missing indicator values remain gaps."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: finite_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite_json(v) for v in value]
    return value


def safe_json(value):
    return json.dumps(finite_json(value), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).replace("</", "<\\/")


def relative_url(target, directory):
    return Path(os.path.relpath(target, directory)).as_posix()


def ensure_assets(root):
    source = SOURCE_ROOT / "assets"
    destination = root / "assets"
    if destination.resolve() != source.resolve():
        shutil.copytree(source, destination, dirs_exist_ok=True)


def _fix_chart_script(script):
    script = script.replace("v.amount", "v.value")
    script = script.replace(" : '#3b82f6' } })));", " : '#3b82f6' } }));")
    # Stored tuples retain the historical [date, open, close, high, low] schema.
    # ECharts consumes [open, close, low, high].
    script = re.sub(
        r"\[([cd])\[1\],\s*\1\[2\],\s*\1\[3\],\s*\1\[4\]\]",
        lambda m: f"[{m[1]}[1], {m[1]}[2], {m[1]}[4], {m[1]}[3]]", script)
    script = re.sub(r"min:\s*0,\s*max:\s*100", "scale: true", script)
    script = re.sub(r"(?<!async )function (showChart|openChart)\(",
                    r"async function \1(", script)
    script = re.sub(
        r"const (data|d)\s*=\s*(?:stockCharts|chartsData)\[(\w+)\];",
        r"const \1 = await SiteReports.loadStock(\2);", script)
    display_name = re.search(r"async function showChart\(\w+,\s*(\w+)\)", script)
    if display_name:
        name = display_name[1]
        script = re.sub(r"(const (data|d) = await SiteReports.loadStock\((\w+)\);)"
                        rf"(?!\s*{name}\s*=)",
                        lambda m: m[1] + f"\n            {name} = {name} || {m[2]}?.name || {m[3]};", script)
    script = re.sub(
        r"if\s*\(!([a-z]+)\)\s*\{\s*alert\([^;]*\);\s*return;\s*\}",
        r"if (!\1) { return; }", script)
    # site.js observes chart containers once, including modal visibility changes.
    script = re.sub(
        r"window.addEventListener\('resize',\s*(?:\(\)\s*=>|function\(\))"
        r"\s*\{[^{}]*\}\s*\);", "", script)
    script = re.sub(r"function closeModal\(\)\s*\{(?!\s*SiteReports.cancel)",
                    "function closeModal() { SiteReports.cancel();", script)
    script = script.replace("setTimeout(() => {", "requestAnimationFrame(() => { if (!SiteReports.isCurrent(d)) return;")
    script = re.sub(r"\},\s*100\);", "});", script)
    script = re.sub(r"link.click\(\);(?!\s*setTimeout\(\(\) => URL.revokeObjectURL)",
                    "link.click();\n            setTimeout(() => URL.revokeObjectURL(link.href), 0);", script)
    return script


def optimize_html(content, output_path, root=None):
    """Upgrade one page; repeated runs preserve content and are idempotent."""
    root = Path(root or Path.cwd()).resolve()
    output_path = Path(output_path).resolve()
    directory = output_path.parent
    asset = relative_url(root / "assets", directory)
    chart_match = CHART_DECL.search(content)
    lazy = bool(chart_match) or 'data-chart-base="' in content

    def externalize_plotly(match):
        body = match[2]
        if len(body) < 1_000_000 or 'plotly.js v' not in body[:200]:
            return match[0]
        digest = hashlib.sha256(body.encode('utf-8')).hexdigest()[:12]
        bundle = root / 'assets/vendor' / f'plotly-{digest}.min.js'
        bundle.parent.mkdir(parents=True, exist_ok=True)
        if not bundle.exists():
            bundle.write_text(body, encoding='utf-8')
        return f'<script src="{relative_url(bundle, directory)}"></script>'

    content = SCRIPT.sub(externalize_plotly, content)
    chart_match = CHART_DECL.search(content)

    if chart_match:
        chart_data, end = json.JSONDecoder().raw_decode(content, chart_match.end())
        if chart_data:
            data_dir = directory / (output_path.stem + ".data")
            data_dir.mkdir(parents=True, exist_ok=True)
            manifest = []
            for code, data in chart_data.items():
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", code):
                    raise ValueError(f"Invalid chart code: {code!r}")
                (data_dir / f"{code}.json").write_text(safe_json(data), encoding="utf-8")
                manifest.append(code)
            (data_dir / "manifest.json").write_text(safe_json(manifest), encoding="utf-8")
            content = content[:chart_match.start()] + content[end + 1:]
            base = html.escape(data_dir.name + "/", quote=True)
            content = re.sub(r"<body\b", f'<body data-chart-base="{base}"', content, count=1)
            content = content.replace("</head>",
                f'<meta name="report-stock-count" content="{len(manifest)}">\n</head>', 1)
        else:
            lazy = False

    if lazy:
        def fix(match):
            if "src=" in match[1] or "application/json" in match[1]:
                return match[0]
            # Only the stock modal script needs the compatibility rewrite.
            if re.search(r"(?:async )?function (showChart|openChart)\(", match[2]):
                return f"<script{match[1]}>" + _fix_chart_script(match[2]) + "</script>"
            return match[0]
        # Avoid reprocessing already upgraded scripts (object URL cleanup etc.).
        if f'data-static-version="{STATIC_VERSION}"' not in content:
            content = SCRIPT.sub(fix, content)
        content = re.sub(r'<script\b[^>]*src=["\'][^"\']*echarts[^"\']*["\'][^>]*>\s*</script>',
                         "", content, flags=re.I)
        if "data-echarts-src=" not in content:
            content = re.sub(r"<body\b", f'<body data-echarts-src="{asset}/vendor/echarts-5.5.0.min.js"',
                             content, count=1)

    content = re.sub(r'<script\s+src=["\']https://cdn.tailwindcss.com[^"\']*["\']\s*>\s*</script>',
                     f'<link rel="stylesheet" href="{asset}/tailwind.css">', content)
    content = re.sub(r'https://cdn.jsdelivr.net/npm/echarts@[^/]+/dist/echarts.min.js',
                     f'{asset}/vendor/echarts-5.5.0.min.js', content)
    content = re.sub(r'\s*<link\b[^>]*href=["\']https://fonts.googleapis.com[^>]*>', "", content)
    if f'href="{asset}/site.css"' not in content:
        content = content.replace("</head>",
            f'<link rel="stylesheet" href="{asset}/site.css">\n'
            f'<script src="{asset}/site.js" defer></script>\n</head>', 1)
    content = re.sub(r'data-static-version="\d+"', f'data-static-version="{STATIC_VERSION}"', content)
    if f'data-static-version="{STATIC_VERSION}"' not in content:
        content = re.sub(r"<body\b", f'<body data-static-version="{STATIC_VERSION}"', content, count=1)
    content = re.sub(r'<span class="close" onclick="closeModal\(\)">(.*?)</span>',
        r'<button type="button" class="close" aria-label="关闭图表" onclick="closeModal()">\1</button>',
        content, flags=re.S)

    home = relative_url(root / "index.html", directory)
    # Correct home links without changing links to a section's own index.
    content = re.sub(r'(<a\b[^>]*href=")[^"]*("[^>]*>\s*←\s*返回首页)',
                     lambda m: m[1] + home + m[2], content)
    if output_path != root / "index.html" and "返回首页" not in content:
        content = re.sub(r'(<body\b[^>]*>)',
            lambda m: m[0] + f'\n<nav class="site-nav" aria-label="页面导航">'
            f'<a href="{home}">← 返回首页</a></nav>', content, count=1)

    def check_link(match):
        attrs, url, inside = match[1], html.unescape(match[2]), match[3]
        if re.match(r"(?:[a-z]+:|//|#)", url, re.I):
            return match[0]
        target = (directory / url.split("#")[0].split("?")[0]).resolve()
        if not target.is_file():
            return (f'<span class="site-unavailable" data-unavailable-href="'
                    f'{html.escape(url, quote=True)}" title="该日期尚无此报告">暂无报告</span>')
        return match[0]
    content = re.sub(r'<a\b([^>]*?)href="([^"]+)"[^>]*>(.*?)</a>',
                     check_link, content, flags=re.S)
    # A later generator may have produced a previously missing sibling report.
    content = re.sub(r'<span class="site-unavailable" data-unavailable-href="([^"]+)"[^>]*>.*?</span>',
        lambda m: (f'<a href="{m[1]}">查看报告</a>'
                   if (directory / html.unescape(m[1])).is_file() else m[0]), content, flags=re.S)
    content = re.sub(r'target="_blank"(?!\s+rel=)', 'target="_blank" rel="noopener"', content)
    if not re.search(r'<meta\s+[^>]*name=["\']viewport["\']', content, re.I):
        content = content.replace('</head>', '<meta name="viewport" content="width=device-width, initial-scale=1">\n</head>', 1)
    return re.sub(r'[ \t]+$', '', content, flags=re.M)


def write_report(path, content):
    """Common output path used by report generators."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ensure_assets(Path.cwd())
    path.write_text(optimize_html(content, path), encoding="utf-8")


def optimize_site(root):
    root = Path(root).resolve()
    ensure_assets(root)
    pages = public_pages(root)
    changed = 0
    for path in pages:
        before = path.read_text(encoding="utf-8")
        after = optimize_html(before, path, root)
        if before != after:
            path.write_text(after, encoding="utf-8")
            changed += 1
    return {"pages": len(pages), "changed": changed}


def build_site(root, destination):
    """Publish only public reports, their referenced CSVs, and static assets."""
    root, destination = Path(root).resolve(), Path(destination).resolve()
    if (destination == root or root.is_relative_to(destination)
            or destination.is_relative_to(root / 'html') or destination.is_relative_to(root / 'assets')):
        raise ValueError("Build directory must not contain the source directory")
    if destination.exists():
        # This directory is owned by this builder; never clear arbitrary paths.
        if not (destination / ".wbzq-site-build").is_file():
            raise ValueError("Refusing to overwrite an unmanaged build directory")
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    (destination / ".wbzq-site-build").touch()
    for name in ["index.html", "reports.json"]:
        shutil.copy2(root / name, destination / name)
    for page in public_pages(root):
        if page.parent == root and page.name != 'index.html':
            shutil.copy2(page, destination / page.name)
            data = page.with_suffix('.data')
            if data.is_dir():
                shutil.copytree(data, destination / data.name)
    shutil.copytree(root / "assets", destination / "assets")
    shutil.copytree(root / "html", destination / "html")
    index = json.loads((root / "reports.json").read_text(encoding="utf-8"))
    for report in index["reports"]:
        for value in report.values():
            if isinstance(value, str) and re.fullmatch(r"(?:c154|c432|macd|multi_indicator)_result_\d{8}\.csv", value):
                shutil.copy2(root / value, destination / value)
    (destination / ".nojekyll").touch()
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["optimize", "build"])
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", default="dist")
    args = parser.parse_args()
    if args.command == "optimize":
        print(json.dumps(optimize_site(args.root)))
    else:
        print(build_site(args.root, Path(args.root) / args.output))
