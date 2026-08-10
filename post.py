#!/usr/bin/env python3
"""Publish a post: markdown in, live indexed page out.

    python post.py new "How I fixed the thing" draft.md
    python post.py new "Quick note"                 # opens an empty draft for you
    python post.py list
    python post.py rebuild                          # regenerate sitemap, feed, index

Writes posts/<slug>.html from the site template, updates the home page list,
regenerates sitemap.xml and feed.xml, and commits. Add --submit and it hands the
URL straight to the indexer.

Standard library only, so plain `python post.py` works with no virtualenv.
"""

import argparse
import html
import json
import re
import subprocess
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
MANIFEST = ROOT / "posts.json"
INDEXER = ROOT.parent / "Indexer"
POSTS_MARKER = "<!-- posts:insert -->"


# ---------------------------------------------------------------------------
# a small markdown subset — enough for a post, no dependencies
# ---------------------------------------------------------------------------

def inline(text):
    """Escape, then re-introduce the inline markup we support."""
    out = html.escape(text, quote=False)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", r'<a href="\2">\1</a>', out)
    out = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<![*\w])\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def markdown(src):
    """Return (html, plain_text). Supports headings, lists, quotes, fences."""
    lines = src.replace("\r\n", "\n").split("\n")
    out, plain = [], []
    para, list_items, list_kind, quote, fence = [], [], None, [], None

    def flush_para():
        if para:
            body = " ".join(para).strip()
            out.append(f"<p>{inline(body)}</p>")
            plain.append(body)
            para.clear()

    def flush_list():
        nonlocal list_kind
        if list_items:
            tag = "ol" if list_kind == "ol" else "ul"
            items = "".join(f"<li>{inline(i)}</li>" for i in list_items)
            out.append(f"<{tag}>{items}</{tag}>")
            plain.extend(list_items)
            list_items.clear()
            list_kind = None

    def flush_quote():
        if quote:
            body = " ".join(quote).strip()
            out.append(f"<blockquote><p>{inline(body)}</p></blockquote>")
            plain.append(body)
            quote.clear()

    def flush_all():
        flush_para()
        flush_list()
        flush_quote()

    for line in lines:
        stripped = line.strip()

        if stripped.startswith("```"):
            if fence is None:
                flush_all()
                fence = []
            else:
                code = html.escape("\n".join(fence))
                out.append(f"<pre><code>{code}</code></pre>")
                fence = None
            continue
        if fence is not None:
            fence.append(line)
            continue

        if not stripped:
            flush_all()
            continue

        heading = re.match(r"^(#{1,4})\s+(.*)$", stripped)
        if heading:
            flush_all()
            level = min(len(heading.group(1)) + 1, 4)  # never a second h1
            text = heading.group(2)
            out.append(f"<h{level}>{inline(text)}</h{level}>")
            plain.append(text)
            continue

        if stripped in ("---", "***"):
            flush_all()
            continue

        if stripped.startswith("> "):
            flush_para()
            flush_list()
            quote.append(stripped[2:])
            continue

        bullet = re.match(r"^[-*+]\s+(.*)$", stripped)
        number = re.match(r"^\d+[.)]\s+(.*)$", stripped)
        if bullet or number:
            flush_para()
            flush_quote()
            kind = "ol" if number else "ul"
            if list_kind and kind != list_kind:
                flush_list()
            list_kind = kind
            list_items.append((number or bullet).group(1))
            continue

        flush_quote()
        flush_list()
        para.append(stripped)

    if fence is not None:  # unterminated fence
        out.append(f"<pre><code>{html.escape(chr(10).join(fence))}</code></pre>")
    flush_all()
    return "\n    ".join(out), " ".join(plain)


# ---------------------------------------------------------------------------
# page rendering
# ---------------------------------------------------------------------------

PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title_esc}</title>
<meta name="description" content="{description}">
<link rel="canonical" href="{url}">
<link rel="stylesheet" href="/assets/style.css">
<link rel="alternate" type="application/atom+xml" href="/feed.xml" title="{author} — posts">
<meta property="og:title" content="{title_esc}">
<meta property="og:description" content="{description}">
<meta property="og:url" content="{url}">
<meta property="og:type" content="article">
<script type="application/ld+json">
{{
  "@context": "https://schema.org",
  "@type": "BlogPosting",
  "headline": {title_json},
  "description": {description_json},
  "author": {{ "@type": "Person", "name": "{author}" }},
  "datePublished": "{date}",
  "mainEntityOfPage": "{url}"
}}
</script>
</head>
<body>

<header class="site">
  <div class="wrap">
    <a class="wordmark" href="/">{author}</a>
    <nav>
      <a href="/">Home</a>
      <a href="/projects/indexer.html">Indexer</a>
    </nav>
  </div>
</header>

<main class="wrap">
  <article>
    <h1>{title_esc}</h1>
    <time class="stamp" datetime="{date}">{date_human}</time>

    {body}
  </article>
</main>

<footer class="site">
  <div class="wrap">
    <p><a href="/">Back home</a> · <a href="/feed.xml">Feed</a></p>
  </div>
</footer>

</body>
</html>
"""


def slugify(title):
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
    return slug or "post"


def human_date(iso):
    return datetime.strptime(iso, "%Y-%m-%d").strftime("%-d %B %Y").lstrip("0") \
        if sys.platform != "win32" else \
        datetime.strptime(iso, "%Y-%m-%d").strftime("%d %B %Y").lstrip("0")


def load_manifest():
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def save_manifest(data):
    MANIFEST.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# generated files
# ---------------------------------------------------------------------------

def write_sitemap(data):
    site = data["site"]
    rows = []
    for page in sorted(data["pages"], key=lambda p: (p["path"] != "/", p["path"])):
        priority = "1.0" if page["path"] == "/" else "0.8"
        freq = "weekly" if page["path"] == "/" else "monthly"
        rows.append(
            f"  <url>\n"
            f"    <loc>{site}{page['path']}</loc>\n"
            f"    <lastmod>{page['date']}</lastmod>\n"
            f"    <changefreq>{freq}</changefreq>\n"
            f"    <priority>{priority}</priority>\n"
            f"  </url>"
        )
    (ROOT / "sitemap.xml").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "\n".join(rows) + "\n</urlset>\n",
        encoding="utf-8",
    )


def write_feed(data):
    """Atom, so the indexer can ping the WebSub hubs on a push."""
    site = data["site"]
    posts = [p for p in data["pages"] if p["kind"] in ("post", "project")]
    posts.sort(key=lambda p: p["date"], reverse=True)
    updated = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    entries = []
    for page in posts:
        url = site + page["path"]
        entries.append(
            f"  <entry>\n"
            f"    <title>{html.escape(page['title'])}</title>\n"
            f"    <link href=\"{url}\"/>\n"
            f"    <id>{url}</id>\n"
            f"    <updated>{page['date']}T00:00:00Z</updated>\n"
            f"    <summary>{html.escape(page['description'])}</summary>\n"
            f"  </entry>"
        )

    (ROOT / "feed.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<feed xmlns="http://www.w3.org/2005/Atom">\n'
        f"  <title>{html.escape(data['author'])} — posts</title>\n"
        f'  <link href="{site}/"/>\n'
        f'  <link rel="self" href="{site}/feed.xml"/>\n'
        f"  <id>{site}/</id>\n"
        f"  <updated>{updated}</updated>\n"
        f"  <author><name>{html.escape(data['author'])}</name></author>\n"
        + "\n".join(entries) + "\n</feed>\n",
        encoding="utf-8",
    )


def update_home(data):
    """Keep the home page list in sync — internal links are what stop a post
    being an orphan, and orphans are the pages Google ignores."""
    index = ROOT / "index.html"
    source = index.read_text(encoding="utf-8")
    if POSTS_MARKER not in source:
        print("  note: no posts list marker in index.html, skipping home update")
        return

    posts = [p for p in data["pages"] if p["kind"] == "post"]
    posts.sort(key=lambda p: p["date"], reverse=True)
    items = "\n".join(
        f'      <li>\n'
        f'        <a href="{p["path"]}">{html.escape(p["title"])}</a>\n'
        f'        <p>{html.escape(p["description"])}</p>\n'
        f'      </li>'
        for p in posts
    )
    head, _, tail = source.partition(POSTS_MARKER)
    tail = tail.split("</ul>", 1)[1]
    index.write_text(f"{head}{POSTS_MARKER}\n{items}\n    </ul>{tail}", encoding="utf-8")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------

def cmd_new(args):
    data = load_manifest()
    today = date.today().isoformat()
    slug = args.slug or slugify(args.title)
    path = f"/posts/{slug}.html"
    url = data["site"] + path

    if args.source:
        src_file = Path(args.source)
        if not src_file.exists():
            sys.exit(f"error: no such file: {src_file}")
        source = src_file.read_text(encoding="utf-8")
    else:
        draft = ROOT / "drafts" / f"{slug}.md"
        draft.parent.mkdir(exist_ok=True)
        if not draft.exists():
            draft.write_text(
                f"Open this file, write the post, then run:\n\n"
                f"    python post.py new \"{args.title}\" drafts/{slug}.md\n",
                encoding="utf-8",
            )
        print(f"draft created: {draft}")
        print("write it, then re-run with the file path as the second argument.")
        return

    body, plain = markdown(source)
    words = len(plain.split())
    description = args.description or (plain[:152].rsplit(" ", 1)[0] + "…" if len(plain) > 155 else plain)

    page = PAGE.format(
        title_esc=html.escape(args.title),
        title_json=json.dumps(args.title),
        description=html.escape(description, quote=True),
        description_json=json.dumps(description),
        url=url,
        author=html.escape(data["author"]),
        date=today,
        date_human=human_date(today),
        body=body,
    )

    target = ROOT / "posts" / f"{slug}.html"
    target.parent.mkdir(exist_ok=True)
    existed = target.exists()
    target.write_text(page, encoding="utf-8")

    data["pages"] = [p for p in data["pages"] if p["path"] != path]
    data["pages"].append({"path": path, "title": args.title,
                          "description": description, "date": today, "kind": "post"})
    save_manifest(data)
    write_sitemap(data)
    write_feed(data)
    update_home(data)

    print(f"{'updated' if existed else 'created'} {target.relative_to(ROOT)}  ({words} words)")
    if words < 300:
        print(f"  warning: {words} words. Under ~300 Google usually files it as "
              "'Crawled - currently not indexed'. Worth more depth before publishing.")
    print("  sitemap.xml, feed.xml and the home page list updated")

    if args.commit or args.submit:
        run_git(f"Publish: {args.title}")
    if args.submit:
        submit(url)
    else:
        print(f"\nnext:  git push   then   python post.py submit {url}")


def run_git(message):
    subprocess.run(["git", "add", "-A"], cwd=ROOT, check=True)
    result = subprocess.run(["git", "commit", "-m", message], cwd=ROOT,
                            capture_output=True, text=True)
    print("  committed" if result.returncode == 0 else f"  git: {result.stdout.strip()}")


def submit(url):
    """Hand the URL to the indexer in the sibling folder."""
    python = INDEXER / ".venv" / "Scripts" / "python.exe"
    if not python.exists():
        python = INDEXER / ".venv" / "bin" / "python"
    if not python.exists():
        print(f"  indexer venv not found at {INDEXER}, skipping submission")
        return
    print()
    subprocess.run([str(python), "indexer.py", "submit", url], cwd=INDEXER)


def cmd_list(args):
    data = load_manifest()
    for page in sorted(data["pages"], key=lambda p: p["date"], reverse=True):
        print(f"{page['date']}  {page['kind']:<8} {page['path']}")
        print(f"            {page['title']}")


def cmd_rebuild(args):
    data = load_manifest()
    write_sitemap(data)
    write_feed(data)
    update_home(data)
    print("sitemap.xml, feed.xml and the home page list regenerated")


def cmd_submit(args):
    submit(args.url)


def main():
    parser = argparse.ArgumentParser(prog="post", description="Publish and index a post.")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("new", help="publish a post from a markdown file")
    p.add_argument("title")
    p.add_argument("source", nargs="?", help="markdown file (omit to create a draft)")
    p.add_argument("--slug")
    p.add_argument("--description")
    p.add_argument("--commit", action="store_true", help="git commit when done")
    p.add_argument("--submit", action="store_true", help="commit, then send to the indexer")
    p.set_defaults(func=cmd_new)

    p = sub.add_parser("list", help="everything in the manifest")
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("rebuild", help="regenerate sitemap, feed and home page list")
    p.set_defaults(func=cmd_rebuild)

    p = sub.add_parser("submit", help="send one URL to the indexer")
    p.add_argument("url")
    p.set_defaults(func=cmd_submit)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
