#!/usr/bin/env python3
import os, shutil, re, pathlib, sys, html
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markdown import markdown
from datetime import datetime, timezone
from re import sub as _re_sub

BASE = pathlib.Path(__file__).parent.resolve()
CONTENT = BASE / "content"
TEMPLATES = BASE / "templates"
STATIC = BASE / "static"
DIST = BASE / "dist"

# ---------------- Helpers ---------------------------------------------------

fm_re = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.S)

def parse_md(path: pathlib.Path):
    raw = path.read_text(encoding="utf-8")
    m = fm_re.match(raw)
    fm = {}
    body_md = raw
    if m:
        fm = yaml.safe_load(m.group(1)) or {}
        body_md = m.group(2)
    html_out = markdown(body_md, extensions=["fenced_code", "tables"])
    return fm, html_out

def slugify(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9\-]+", "-", name.strip().lower()).strip("-")
    return s or "untitled"

def rfc822(dt_str):
    if not dt_str:
        return None
    try:
        dt = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
    except ValueError:
        dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.strftime("%a, %d %b %Y %H:%M:%S %z")

def strip_html_text(text, max_len=300):
    s = _re_sub(r"<[^>]+>", "", text)
    s = _re_sub(r"\s+", " ", s).strip()
    return (s[:max_len] + "…") if len(s) > max_len else s

def copy_static():
    if STATIC.exists():
        dest = DIST / "static"
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(STATIC, dest)

def load_config():
    cfg = BASE / "config.yaml"
    return yaml.safe_load(cfg.read_text(encoding="utf-8")) if cfg.exists() else {}


# ---------------- Jinja ----------------------------------------------------

env = Environment(
    loader=FileSystemLoader(str(TEMPLATES)),
    autoescape=select_autoescape(["html"])
)

def render_tpl(name, **ctx):
    return env.get_template(name).render(**ctx)


# ---------------- Build ----------------------------------------------------

def build():
    # clean dist
    if DIST.exists():
        shutil.rmtree(DIST)
    DIST.mkdir(parents=True, exist_ok=True)

    site = load_config()
    base_url = site.get("base_url", "").rstrip("/")
    base_href = base_url if base_url else ""
    site_url = site.get("site_url", "https://www.kovalski.info").rstrip("/")

    copy_static()

    posts = []
    tags_map = {}

    # Render Markdown -> HTML pages
    for root, _, files in os.walk(CONTENT):
        root_p = pathlib.Path(root)
        for file in files:
            if not file.endswith(".md"):
                continue

            src = root_p / file
            rel = src.relative_to(CONTENT)
            fm, body_html = parse_md(src)

            title = fm.get("title", rel.stem)
            template = fm.get("template", "page")
            raw_date = fm.get("date")
            if isinstance(raw_date, datetime):
                date = raw_date.strftime("%Y-%m-%d")
            elif isinstance(raw_date, str):
                date = raw_date
            else:
                date = None
            tags = fm.get("tags", [])

            out_rel = rel.with_suffix(".html")
            out_path = DIST / out_rel
            out_path.parent.mkdir(parents=True, exist_ok=True)

            html_rendered = render_tpl(
                f"{template}.html",
                title=title,
                date=date,
                tags=tags,
                body=body_html,
                base=base_href,
                site=site,
            )
            out_path.write_text(html_rendered, encoding="utf-8")

            # Collect posts only from content/posts/
            if "posts" in out_rel.parts:
                post_href = f"{base_href}/{out_rel.as_posix()}"
                sort_key = f"{date or ''}{title}"
                post_info = {
                    "title": title,
                    "href": post_href,
                    "date": date,
                    "sort": sort_key,
                    "tags": tags,
                    "file": out_path
                }
                posts.append(post_info)

                for t in tags:
                    slug = slugify(t)
                    tags_map.setdefault(slug, {"name": t, "posts": []})["posts"].append(post_info)

    # POSTS INDEX -------------------------------------------
    if posts:
        posts.sort(key=lambda x: x["sort"], reverse=True)
        post_items = "".join(
            f'<li><a href="{p["href"]}">{p["title"]}</a>{(" — " + p["date"]) if p["date"] else ""}</li>'
            for p in posts
        )
        posts_dir = DIST / "posts"
        posts_dir.mkdir(exist_ok=True)
        posts_index_html = render_tpl(
            "page.html",
            title="Posts",
            body=f"<ul class='posts'>{post_items}</ul>",
            base=base_href,
            site=site,
        )
        (posts_dir / "index.html").write_text(posts_index_html, encoding="utf-8")

    # TAG SYSTEM --------------------------------------------
    if tags_map:
        tags_sorted = sorted(tags_map.items(), key=lambda x: x[0])
        tags_list = [(v["name"], len(v["posts"])) for _, v in tags_sorted]

        tags_dir = DIST / "tags"
        tags_dir.mkdir(exist_ok=True)

        tags_index_html = render_tpl(
            "tags.html",
            title="Tags",
            tags=tags_list,
            base=base_href,
            site=site,
        )
        (tags_dir / "index.html").write_text(tags_index_html, encoding="utf-8")

        for slug, data in tags_sorted:
            posts_sorted = sorted(data["posts"], key=lambda x: x["sort"], reverse=True)
            tag_html = render_tpl(
                "tag.html",
                title=f"Tag: {data['name']}",
                tag=data["name"],
                posts=posts_sorted,
                base=base_href,
                site=site,
            )
            (tags_dir / slug / "index.html").parent.mkdir(parents=True, exist_ok=True)
            (tags_dir / slug / "index.html").write_text(tag_html, encoding="utf-8")

    # RSS ---------------------------------------------------
    if posts:
        posts_sorted = sorted(posts, key=lambda x: x["sort"], reverse=True)
        rss_title = site.get("rss_title", site.get("site_name", "Feed"))
        rss_path = site.get("rss_path", "/rss.xml")

        items_xml = []
        for p in posts_sorted:
            abs_link = f"{site_url}{p['href']}"
            pubdate = rfc822(p["date"])
            desc_html = p["file"].read_text(encoding="utf-8")
            desc = html.escape(strip_html_text(desc_html, 400))

            items_xml.append(
                "  <item>\n"
                f"    <title>{html.escape(p['title'])}</title>\n"
                f"    <link>{abs_link}</link>\n"
                f"    <guid isPermaLink='true'>{abs_link}</guid>\n"
                + (f"    <pubDate>{pubdate}</pubDate>\n" if pubdate else "")
                + f"    <description>{desc}</description>\n"
                "  </item>\n"
            )

        last_build = rfc822(datetime.utcnow().replace(tzinfo=timezone.utc).isoformat())

        rss_xml = (
            "<?xml version='1.0' encoding='UTF-8'?>\n"
            "<rss version='2.0'>\n"
            " <channel>\n"
            f"  <title>{html.escape(rss_title)}</title>\n"
            f"  <link>{site_url}</link>\n"
            f"  <description>{html.escape(site.get('site_name',''))}</description>\n"
            f"  <lastBuildDate>{last_build}</lastBuildDate>\n"
            + "".join(items_xml) +
            " </channel>\n"
            "</rss>\n"
        )

        (DIST / rss_path.lstrip("/")).write_text(rss_xml, encoding="utf-8")

    # SITEMAP -----------------------------------------------
    urls = []
    for root, _, files in os.walk(DIST):
        for f in files:
            if f.endswith(".html"):
                rel = (pathlib.Path(root) / f).relative_to(DIST)
                url = "/" + rel.as_posix()
                if url.endswith("index.html"):
                    url = url[:-10]
                urls.append(url)

    sitemap_xml = (
        "<?xml version='1.0' encoding='UTF-8'?>\n"
        "<urlset xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>\n"
        + "".join(f"  <url><loc>{site_url}{u}</loc></url>\n" for u in urls) +
        "</urlset>\n"
    )
    (DIST / "sitemap.xml").write_text(sitemap_xml, encoding="utf-8")

    # ROBOTS ------------------------------------------------
    (DIST / "robots.txt").write_text(
        f"User-agent: *\nAllow: /\nSitemap: {site_url}/sitemap.xml\n",
        encoding="utf-8"
    )

    # CNAME -------------------------------------------------
    (DIST / "CNAME").write_text("www.kovalski.info", encoding="utf-8")

    print(f"✅ Build complete → {DIST}")


# ---------------- CLI ------------------------------------------------------

if __name__ == "__main__":
    try:
        build()
    except Exception as e:
        print("❌ Build failed:", e)
        sys.exit(1)
