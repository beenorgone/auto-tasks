#!/usr/bin/env python3
"""Turn a Facebook photo album (photos + captions) into an ebook with a TOC.

Pipeline:  fetch  ->  build  ->  send
  fetch  collects album metadata, captions and images into a work directory
         (Graph API, a logged-in Playwright browser profile, or a Facebook
         "Download your information" export).
  build  writes an EPUB 3 (Kindle's preferred format) and/or a PDF.
  send   mails the ebook to a Send-to-Kindle address over SMTP.

Only the standard library is required for fetch (graph/export), EPUB and send.
Optional: Pillow (downscale images), reportlab (PDF), playwright (browser fetch).
"""

from __future__ import annotations

import argparse
import html
import json
import mimetypes
import os
import random
import re
import smtplib
import sys
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Callable, Iterable


APP_ROOT = Path(__file__).resolve().parent
DEFAULT_WORK_ROOT = APP_ROOT / "fb-ebooks"
DEFAULT_PROFILE_DIR = Path.home() / ".cache" / "fb-album-ebook" / "browser-profile"
GRAPH_VERSION = "v21.0"
KINDLE_MAX_BYTES = 50 * 1024 * 1024
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
)
FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/Library/Fonts/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


# --------------------------------------------------------------------------- model


@dataclass
class Photo:
    id: str
    caption: str = ""
    created_time: str = ""
    image_url: str = ""
    file: str = ""  # path relative to the work dir, e.g. images/0001.jpg


@dataclass
class Album:
    id: str
    title: str = ""
    description: str = ""
    author: str = ""
    source_url: str = ""
    photos: list[Photo] = field(default_factory=list)

    def save(self, work_dir: Path) -> None:
        work_dir.mkdir(parents=True, exist_ok=True)
        target = work_dir / "album.json"
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(target)

    @classmethod
    def load(cls, work_dir: Path) -> "Album":
        data = json.loads((work_dir / "album.json").read_text(encoding="utf-8"))
        photos = [Photo(**p) for p in data.pop("photos", [])]
        return cls(**data, photos=photos)


@dataclass
class Chapter:
    title: str
    caption: str
    photos: list[Photo]


def log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- helpers


def parse_album_id(value: str) -> str:
    """Accept an album URL in any common Facebook shape, or a bare numeric id."""
    value = value.strip()
    if value.isdigit():
        return value
    query = urllib.parse.parse_qs(urllib.parse.urlparse(value).query)
    for key in ("set", "album_id"):
        for item in query.get(key, []):
            match = re.fullmatch(r"(?:a\.)?(\d+)", item)
            if match:
                return match.group(1)
    match = re.search(r"/(?:photos|albums)/a\.(\d+)", value) or re.search(r"\ba\.(\d+)", value)
    if match:
        return match.group(1)
    raise ValueError(f"Cannot find an album id in {value!r}")


def fix_mojibake(text: str) -> str:
    """Facebook data exports store UTF-8 bytes as latin-1 code points."""
    try:
        return text.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def first_line_title(caption: str, limit: int = 80) -> str:
    for line in caption.splitlines():
        line = re.sub(r"\s+", " ", line).strip(" -–—•*#:|")
        if line:
            return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"
    return ""


def group_chapters(photos: list[Photo], mode: str = "caption", untitled: str = "Ảnh") -> list[Chapter]:
    """caption: a captioned photo opens a chapter, following uncaptioned photos join it.
    photo:   one chapter per photo."""
    chapters: list[Chapter] = []
    for index, photo in enumerate(photos, start=1):
        caption = photo.caption.strip()
        if mode == "photo" or caption or not chapters:
            title = first_line_title(caption) or f"{untitled} {index}"
            chapters.append(Chapter(title=title, caption=caption, photos=[photo]))
        else:
            chapters[-1].photos.append(photo)
    return chapters


def http_get(url: str, timeout: int = 60) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def http_get_json(url: str) -> dict[str, Any]:
    return json.loads(http_get(url).decode("utf-8"))


def guess_ext(data: bytes, fallback: str = ".jpg") -> str:
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return fallback


def store_image(data: bytes, target_stem: Path, max_side: int) -> Path:
    """Write an image, normalising to a Kindle-friendly JPEG when Pillow is available."""
    try:
        from io import BytesIO

        from PIL import Image, ImageOps
    except ImportError:
        ext = guess_ext(data)
        if ext == ".webp":
            log("  ! WEBP image kept as-is; install Pillow to convert it for Kindle")
        target = target_stem.with_suffix(ext)
        target.write_bytes(data)
        return target
    with Image.open(BytesIO(data)) as image:
        image = ImageOps.exif_transpose(image)
        if image.mode not in ("RGB", "L"):
            image = image.convert("RGB")
        if max_side and max(image.size) > max_side:
            image.thumbnail((max_side, max_side))
        target = target_stem.with_suffix(".jpg")
        image.save(target, "JPEG", quality=85, optimize=True, progressive=True)
    return target


def download_images(
    album: Album,
    work_dir: Path,
    max_side: int,
    getter: Callable[[str], bytes] = http_get,
) -> None:
    images_dir = work_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    total = len(album.photos)
    for index, photo in enumerate(album.photos, start=1):
        if photo.file and (work_dir / photo.file).exists():
            continue
        if not photo.image_url:
            log(f"  ! photo {photo.id} has no image url, skipped")
            continue
        try:
            data = getter(photo.image_url)
        except Exception as error:  # network hiccup: keep going, re-run resumes
            log(f"  ! download failed for photo {photo.id}: {error}")
            continue
        stored = store_image(data, images_dir / f"{index:04d}", max_side)
        photo.file = stored.relative_to(work_dir).as_posix()
        log(f"  [{index}/{total}] {photo.file}")
        album.save(work_dir)


# --------------------------------------------------------------------------- sources


def fetch_graph(album_id: str, token: str, get_json: Callable[[str], dict] = http_get_json) -> Album:
    """Graph API: works for albums of Pages you manage (use a Page access token)."""
    base = f"https://graph.facebook.com/{GRAPH_VERSION}"
    meta_query = urllib.parse.urlencode(
        {"fields": "name,description,from,link", "access_token": token}
    )
    meta = get_json(f"{base}/{album_id}?{meta_query}")
    album = Album(
        id=album_id,
        title=meta.get("name", ""),
        description=meta.get("description", ""),
        author=(meta.get("from") or {}).get("name", ""),
        source_url=meta.get("link", ""),
    )
    photo_query = urllib.parse.urlencode(
        {"fields": "id,name,created_time,images", "limit": 100, "access_token": token}
    )
    url: str | None = f"{base}/{album_id}/photos?{photo_query}"
    while url:
        page = get_json(url)
        for item in page.get("data", []):
            images = sorted(item.get("images", []), key=lambda i: i.get("width", 0), reverse=True)
            album.photos.append(
                Photo(
                    id=str(item["id"]),
                    caption=item.get("name", ""),
                    created_time=item.get("created_time", ""),
                    image_url=images[0]["source"] if images else "",
                )
            )
        url = (page.get("paging") or {}).get("next")
    # Graph returns newest first; albums read best in upload order.
    album.photos.reverse()
    return album


def _export_image_uri(album_json: Path, uri: str) -> str:
    """Export URIs are relative to the export root, which sits a few levels above the JSON."""
    if not uri:
        return ""
    for ancestor in album_json.parents:
        if (ancestor / uri).exists():
            return (ancestor / uri).resolve().as_uri()
    return ""


def load_export(path: Path, album_hint: str = "") -> Album:
    """Read one album from a Facebook 'Download your information' JSON export.

    `path` is either the album JSON (…/posts/album/N.json) or the export root, in
    which case the album whose name contains `album_hint` is used."""
    candidates = [path] if path.is_file() else sorted(path.rglob("album/*.json"))
    for candidate in candidates:
        data = json.loads(candidate.read_text(encoding="utf-8"))
        name = fix_mojibake(data.get("name", ""))
        if album_hint and album_hint.casefold() not in name.casefold() and len(candidates) > 1:
            continue
        album = Album(
            id=re.sub(r"\W+", "-", name).strip("-").lower() or candidate.stem,
            title=name,
            description=fix_mojibake(data.get("description", "")),
        )
        for index, item in enumerate(data.get("photos", []), start=1):
            created = item.get("creation_timestamp")
            album.photos.append(
                Photo(
                    id=str(index),
                    caption=fix_mojibake(item.get("description") or item.get("title") or ""),
                    created_time=(
                        datetime.fromtimestamp(created, timezone.utc).isoformat() if created else ""
                    ),
                    image_url=_export_image_uri(candidate, item.get("uri", "")),
                )
            )
        return album
    raise FileNotFoundError(f"No album JSON matching {album_hint!r} under {path}")


def read_file_url(url: str) -> bytes:
    if url.startswith("file:"):
        return Path(urllib.request.url2pathname(urllib.parse.urlparse(url).path)).read_bytes()
    return http_get(url)


ALBUM_META_JS = r"""
() => {
  const meta = p => (document.querySelector(`meta[property="${p}"]`) || {}).content || '';
  const title = meta('og:title') ||
    document.title.replace(/^\(\d+\)\s*/, '').replace(/\s*\|\s*Facebook\s*$/, '');
  return {title, description: meta('og:description')};
}
"""

PHOTO_LINKS_JS = r"""
() => Array.from(document.querySelectorAll('a[href*="fbid="]')).map(a => a.href)
"""

PHOTO_DETAIL_JS = r"""
() => {
  let best = null;
  for (const img of document.querySelectorAll('img[data-visualcompletion="media-vc-image"]')) {
    const area = (img.naturalWidth || 0) * (img.naturalHeight || 0);
    if (!best || area > best.area) best = {src: img.currentSrc || img.src, area};
  }
  const text = sel => { const el = document.querySelector(sel); return el ? el.innerText.trim() : ''; };
  let caption = text('[role="complementary"] [data-ad-preview="message"]') ||
                text('[role="complementary"] [data-ad-comet-preview="message"]');
  if (!caption) {
    const pane = document.querySelector('[role="complementary"]');
    if (pane) {
      const blocks = Array.from(pane.querySelectorAll('div[dir="auto"], span[dir="auto"]'))
        .filter(el => !el.closest('[role="article"], h2, h3, a, [role="button"], [role="textbox"], form'))
        .map(el => el.innerText.trim())
        .filter(t => t.length > 1);
      blocks.sort((a, b) => b.length - a.length);
      caption = blocks[0] || '';
    }
  }
  const time = document.querySelector('[role="complementary"] a[href*="fbid"] span, [role="complementary"] abbr');
  return {src: best ? best.src : '', caption, created: time ? time.innerText.trim() : ''};
}
"""


def _open_browser(profile_dir: Path, headless: bool):
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SystemExit("Browser source needs: pip install playwright") from error
    profile_dir.mkdir(parents=True, exist_ok=True)
    manager = sync_playwright().start()
    context = manager.chromium.launch_persistent_context(
        str(profile_dir),
        headless=headless,
        locale="vi-VN",
        viewport={"width": 1366, "height": 900},
        executable_path=os.environ.get("FB_EBOOK_CHROMIUM") or None,
    )
    return manager, context


def browser_login(profile_dir: Path) -> None:
    manager, context = _open_browser(profile_dir, headless=False)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto("https://www.facebook.com/login")
        input("Log in to Facebook in the opened window, then press Enter here… ")
    finally:
        context.close()
        manager.stop()


def fetch_browser(
    album_id: str,
    profile_dir: Path,
    headless: bool,
    work_dir: Path,
    limit: int = 0,
    delay: float = 1.5,
) -> Album:
    """Scrape with a real (persistent, logged-in) Chromium profile. Resumable."""
    album_url = f"https://www.facebook.com/media/set/?set=a.{album_id}&type=3"
    album = Album.load(work_dir) if (work_dir / "album.json").exists() else Album(id=album_id)
    album.source_url = album_url
    done = {p.id: p for p in album.photos if p.image_url}
    manager, context = _open_browser(profile_dir, headless)
    try:
        page = context.pages[0] if context.pages else context.new_page()
        page.goto(album_url, wait_until="domcontentloaded")
        page.wait_for_timeout(4000)
        if "/login" in page.url or page.locator('input[name="pass"]').count():
            raise SystemExit("Facebook asks for login: run `fb-album-ebook.py login` first.")
        meta = page.evaluate(ALBUM_META_JS)
        album.title = album.title or meta.get("title", "")
        album.description = album.description or meta.get("description", "")

        order: list[str] = []
        stale_rounds = 0
        while stale_rounds < 4 and not (limit and len(order) >= limit):
            before = len(order)
            for href in page.evaluate(PHOTO_LINKS_JS):
                match = re.search(r"fbid=(\d+)", href)
                if match and f"a.{album_id}" in href and match.group(1) not in order:
                    order.append(match.group(1))
            stale_rounds = stale_rounds + 1 if len(order) == before else 0
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(1500)
        if limit:
            order = order[:limit]
        log(f"Found {len(order)} photos in album “{album.title}”")
        if not order:
            raise SystemExit("No photos found — is the album visible to this account?")

        album.photos = [done.get(fbid) or Photo(id=fbid) for fbid in order]
        for index, photo in enumerate(album.photos, start=1):
            if photo.image_url:
                continue
            page.goto(
                f"https://www.facebook.com/photo/?fbid={photo.id}&set=a.{album_id}",
                wait_until="domcontentloaded",
            )
            try:
                page.wait_for_selector('img[data-visualcompletion="media-vc-image"]', timeout=20000)
            except Exception:
                log(f"  ! photo {photo.id}: image not found, skipped")
                continue
            more = page.locator('[role="complementary"] [role="button"]').filter(
                has_text=re.compile(r"^\s*(See more|Xem thêm)\s*$")
            )
            for i in range(min(more.count(), 3)):
                try:
                    more.nth(i).click(timeout=2000)
                except Exception:
                    pass
            detail = page.evaluate(PHOTO_DETAIL_JS)
            photo.image_url = detail.get("src", "")
            photo.caption = detail.get("caption", "")
            photo.created_time = detail.get("created", "")
            log(f"  [{index}/{len(album.photos)}] {photo.id} {first_line_title(photo.caption, 50)}")
            album.save(work_dir)
            page.wait_for_timeout(int((delay + random.random()) * 1000))
    finally:
        context.close()
        manager.stop()
    return album


# --------------------------------------------------------------------------- EPUB


EPUB_CSS = """
body { font-family: serif; line-height: 1.5; margin: 0 4%; }
h1 { text-align: center; margin-top: 30%; }
h2 { font-size: 1.3em; margin: 1.2em 0 0.6em; page-break-before: always; }
p { margin: 0 0 0.8em; text-indent: 0; }
.meta { text-align: center; color: #555; font-size: 0.9em; }
figure { margin: 0 0 1.2em; text-align: center; page-break-inside: avoid; }
figure img { max-width: 100%; max-height: 95vh; }
nav ol { list-style: none; padding-left: 0; }
nav li { margin: 0.35em 0; }
"""


def _xhtml(title: str, body: str, lang: str, extra_ns: str = "") -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n<!DOCTYPE html>\n'
        f'<html xmlns="http://www.w3.org/1999/xhtml"{extra_ns} xml:lang="{lang}" lang="{lang}">\n'
        f"<head><meta charset=\"utf-8\"/><title>{html.escape(title)}</title>"
        '<link rel="stylesheet" type="text/css" href="style.css"/></head>\n'
        f"<body>\n{body}\n</body>\n</html>\n"
    )


def _paragraphs(text: str) -> str:
    blocks = [b.strip() for b in re.split(r"\n\s*\n", text) if b.strip()]
    return "\n".join(
        "<p>" + "<br/>".join(html.escape(line) for line in block.splitlines()) + "</p>"
        for block in blocks
    )


def build_epub(album: Album, work_dir: Path, output: Path, chapters: list[Chapter], lang: str) -> Path:
    title = album.title or f"Facebook album {album.id}"
    book_id = f"urn:uuid:{uuid.uuid5(uuid.NAMESPACE_URL, 'facebook-album:' + album.id)}"
    modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    images: list[tuple[str, Path]] = []  # (href inside OEBPS, source path)
    image_href: dict[str, str] = {}
    for photo in album.photos:
        if photo.file and (work_dir / photo.file).exists():
            href = f"images/{Path(photo.file).name}"
            image_href[photo.id] = href
            images.append((href, work_dir / photo.file))
    cover_href = images[0][0] if images else ""

    docs: list[tuple[str, str, str]] = []  # (href, nav title, xhtml)
    if cover_href:
        docs.append(
            (
                "cover.xhtml",
                "Bìa" if lang == "vi" else "Cover",
                _xhtml(title, f'<figure><img src="{cover_href}" alt="{html.escape(title)}"/></figure>', lang),
            )
        )
    title_body = f"<h1>{html.escape(title)}</h1>"
    if album.author:
        title_body += f'<p class="meta">{html.escape(album.author)}</p>'
    if album.source_url:
        title_body += f'<p class="meta">{html.escape(album.source_url)}</p>'
    title_body += _paragraphs(album.description)
    docs.append(("title.xhtml", title, _xhtml(title, title_body, lang)))

    chapter_docs: list[tuple[str, str, str]] = []
    for number, chapter in enumerate(chapters, start=1):
        parts = [f'<h2 id="c{number}">{html.escape(chapter.title)}</h2>', _paragraphs(chapter.caption)]
        for photo in chapter.photos:
            if photo.id in image_href:
                parts.append(
                    f'<figure><img src="{image_href[photo.id]}" alt="{html.escape(chapter.title)}"/></figure>'
                )
        chapter_docs.append(
            (f"chapter-{number:04d}.xhtml", chapter.title, _xhtml(chapter.title, "\n".join(parts), lang))
        )

    toc_label = "Mục lục" if lang == "vi" else "Contents"
    nav_items = "\n".join(
        f'<li><a href="{href}">{html.escape(label)}</a></li>' for href, label, _ in chapter_docs
    )
    nav = _xhtml(
        toc_label,
        f'<nav epub:type="toc" id="toc"><h2>{toc_label}</h2><ol>\n{nav_items}\n</ol></nav>',
        lang,
        extra_ns=' xmlns:epub="http://www.idpf.org/2007/ops"',
    )
    all_docs = docs + [("nav.xhtml", toc_label, nav)] + chapter_docs

    ncx_points = "\n".join(
        f'<navPoint id="np{i}" playOrder="{i}"><navLabel><text>{html.escape(label)}</text></navLabel>'
        f'<content src="{href}"/></navPoint>'
        for i, (href, label, _) in enumerate([d for d in all_docs if d[0] != "cover.xhtml"], start=1)
    )
    ncx = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">'
        f'<head><meta name="dtb:uid" content="{book_id}"/></head>'
        f"<docTitle><text>{html.escape(title)}</text></docTitle>"
        f"<navMap>\n{ncx_points}\n</navMap></ncx>\n"
    )

    manifest = ['<item id="css" href="style.css" media-type="text/css"/>',
                '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>']
    spine = []
    for index, (href, _, _) in enumerate(all_docs):
        item_id = f"doc{index}"
        props = ' properties="nav"' if href == "nav.xhtml" else ""
        manifest.append(f'<item id="{item_id}" href="{href}" media-type="application/xhtml+xml"{props}/>')
        spine.append(f'<itemref idref="{item_id}"/>')
    for index, (href, source) in enumerate(images):
        media_type = mimetypes.guess_type(source.name)[0] or "image/jpeg"
        props = ' properties="cover-image"' if href == cover_href else ""
        manifest.append(f'<item id="img{index}" href="{href}" media-type="{media_type}"{props}/>')
    cover_meta = '<meta name="cover" content="img0"/>' if cover_href else ""
    opf = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">\n'
        '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
        f'<dc:identifier id="bookid">{book_id}</dc:identifier>'
        f"<dc:title>{html.escape(title)}</dc:title><dc:language>{lang}</dc:language>"
        + (f"<dc:creator>{html.escape(album.author)}</dc:creator>" if album.author else "")
        + (f"<dc:description>{html.escape(album.description)}</dc:description>" if album.description else "")
        + f'<meta property="dcterms:modified">{modified}</meta>{cover_meta}</metadata>\n'
        f"<manifest>\n{chr(10).join(manifest)}\n</manifest>\n"
        f'<spine toc="ncx">\n{chr(10).join(spine)}\n</spine>\n'
        '<guide><reference type="toc" title="TOC" href="nav.xhtml"/></guide>\n'
        "</package>\n"
    )
    container = (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
        '<rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
        "</rootfiles></container>\n"
    )

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w") as book:
        book.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        book.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/toc.ncx", ncx, compress_type=zipfile.ZIP_DEFLATED)
        book.writestr("OEBPS/style.css", EPUB_CSS, compress_type=zipfile.ZIP_DEFLATED)
        for href, _, content in all_docs:
            book.writestr(f"OEBPS/{href}", content, compress_type=zipfile.ZIP_DEFLATED)
        for href, source in images:
            book.write(source, f"OEBPS/{href}", compress_type=zipfile.ZIP_STORED)
    return output


# --------------------------------------------------------------------------- PDF


def _register_font(font_path: str | None) -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont

    for candidate in [font_path] if font_path else FONT_CANDIDATES:
        if candidate and Path(candidate).exists():
            pdfmetrics.registerFont(TTFont("EbookFont", candidate))
            return "EbookFont"
    log("  ! No Unicode TTF font found (use --font); Vietnamese accents may not render in PDF")
    return "Helvetica"


def build_pdf(
    album: Album,
    work_dir: Path,
    output: Path,
    chapters: list[Chapter],
    lang: str,
    page_size: str = "a5",
    font_path: str | None = None,
) -> Path:
    try:
        from reportlab.lib import pagesizes
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.lib.units import mm
        from reportlab.platypus import Frame, Image, PageBreak, PageTemplate, Paragraph, Spacer
        from reportlab.platypus.doctemplate import BaseDocTemplate
        from reportlab.platypus.tableofcontents import TableOfContents
    except ImportError as error:
        raise SystemExit("PDF output needs: pip install reportlab") from error

    font = _register_font(font_path)
    size = {"a4": pagesizes.A4, "a5": pagesizes.A5, "letter": pagesizes.LETTER}[page_size]
    margin = 12 * mm
    title = album.title or f"Facebook album {album.id}"
    styles = {
        "title": ParagraphStyle("BookTitle", fontName=font, fontSize=22, leading=28, alignment=1, spaceAfter=12),
        "meta": ParagraphStyle("Meta", fontName=font, fontSize=9, leading=12, alignment=1, textColor="#555555"),
        "chapter": ParagraphStyle("ChapterTitle", fontName=font, fontSize=14, leading=18, spaceAfter=8),
        "body": ParagraphStyle("Body", fontName=font, fontSize=10.5, leading=15, spaceAfter=6),
        "toc_head": ParagraphStyle("TocHead", fontName=font, fontSize=16, leading=20, spaceAfter=10),
        "toc": ParagraphStyle("TocEntry", fontName=font, fontSize=10, leading=14),
    }

    class EbookDoc(BaseDocTemplate):
        def __init__(self, filename: str) -> None:
            super().__init__(filename, pagesize=size, leftMargin=margin, rightMargin=margin,
                             topMargin=margin, bottomMargin=margin, title=title, author=album.author)
            frame = Frame(margin, margin, size[0] - 2 * margin, size[1] - 2 * margin, id="body")
            self.addPageTemplates([PageTemplate(id="page", frames=[frame], onPage=self._footer)])

        def _footer(self, canvas, doc) -> None:
            canvas.setFont(font, 8)
            canvas.drawCentredString(size[0] / 2, margin / 2, str(doc.page))

        def afterFlowable(self, flowable) -> None:
            bookmark = getattr(flowable, "_bookmark", None)
            if bookmark:
                key, text = bookmark
                self.canv.bookmarkPage(key)
                self.canv.addOutlineEntry(text, key, level=0, closed=True)
                self.notify("TOCEntry", (0, html.escape(text), self.page, key))

    def para(text: str, style: str) -> Paragraph:
        return Paragraph(html.escape(text).replace("\n", "<br/>"), styles[style])

    frame_w = size[0] - 2 * margin
    frame_h = size[1] - 2 * margin

    def picture(path: Path) -> Image:
        from reportlab.lib.utils import ImageReader

        width, height = ImageReader(str(path)).getSize()
        scale = min(frame_w / width, frame_h * 0.8 / height)
        return Image(str(path), width=width * scale, height=height * scale)

    story: list[Any] = [Spacer(1, frame_h * 0.25), para(title, "title")]
    for meta in (album.author, album.source_url):
        if meta:
            story.append(para(meta, "meta"))
    if album.description:
        story += [Spacer(1, 12), para(album.description, "body")]
    toc = TableOfContents()
    toc.levelStyles = [styles["toc"]]
    story += [PageBreak(), Paragraph("Mục lục" if lang == "vi" else "Contents", styles["toc_head"]), toc]
    for number, chapter in enumerate(chapters, start=1):
        story.append(PageBreak())
        heading = para(chapter.title, "chapter")
        heading._bookmark = (f"c{number}", chapter.title)
        story.append(heading)
        if chapter.caption:
            story.append(para(chapter.caption, "body"))
        for photo in chapter.photos:
            if photo.file and (work_dir / photo.file).exists():
                story += [picture(work_dir / photo.file), Spacer(1, 8)]

    output.parent.mkdir(parents=True, exist_ok=True)
    EbookDoc(str(output)).multiBuild(story)
    return output


# --------------------------------------------------------------------------- send


def build_kindle_message(path: Path, to: str, sender: str, subject: str = "") -> EmailMessage:
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject or path.stem
    message.set_content(f"Ebook: {path.name}\n")
    media_type = {
        ".epub": "application/epub+zip",
        ".pdf": "application/pdf",
    }.get(path.suffix.lower(), "application/octet-stream")
    maintype, subtype = media_type.split("/", 1)
    message.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return message


def send_to_kindle(path: Path, to: str, env: dict[str, str] | None = None) -> None:
    env = dict(os.environ if env is None else env)
    size = path.stat().st_size
    if size > KINDLE_MAX_BYTES:
        raise SystemExit(
            f"{path.name} is {size / 1e6:.1f} MB; Send-to-Kindle accepts ≤ 50 MB. "
            "Rebuild with a smaller --max-image-side or split the album."
        )
    user = env.get("SMTP_USER", "")
    password = env.get("SMTP_PASSWORD", "")
    if not user or not password:
        raise SystemExit("Set SMTP_USER and SMTP_PASSWORD (Gmail: use an App Password).")
    host = env.get("SMTP_HOST", "smtp.gmail.com")
    port = int(env.get("SMTP_PORT", "587"))
    sender = env.get("SMTP_FROM", user)
    message = build_kindle_message(path, to, sender)
    smtp_class = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    with smtp_class(host, port, timeout=120) as server:
        if port != 465:
            server.starttls()
        server.login(user, password)
        server.send_message(message)
    log(f"Sent {path.name} ({size / 1e6:.1f} MB) to {to} from {sender}")
    log("  (the sender must be in Amazon's Approved Personal Document E-mail List)")


# --------------------------------------------------------------------------- CLI


def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|\r\n]+', " ", name).strip()
    return re.sub(r"\s+", " ", name)[:120] or "album"


def resolve_work_dir(args: argparse.Namespace, album_id: str) -> Path:
    return Path(args.work_dir) if args.work_dir else DEFAULT_WORK_ROOT / album_id


def command_fetch(args: argparse.Namespace) -> Path:
    if args.source == "export":
        album = load_export(Path(args.album), args.album_name)
        work_dir = resolve_work_dir(args, album.id)
        album.save(work_dir)
        download_images(album, work_dir, args.max_image_side, getter=read_file_url)
        return work_dir
    album_id = parse_album_id(args.album)
    work_dir = resolve_work_dir(args, album_id)
    if args.source == "graph":
        token = args.token or os.environ.get("FB_ACCESS_TOKEN", "")
        if not token:
            raise SystemExit("Graph source needs --token or FB_ACCESS_TOKEN (a Page access token).")
        album = fetch_graph(album_id, token)
        album.source_url = album.source_url or args.album
        album.save(work_dir)
    else:
        album = fetch_browser(album_id, Path(args.profile), args.headless, work_dir, args.limit, args.delay)
    download_images(album, work_dir, args.max_image_side)
    log(f"Album saved to {work_dir}")
    return work_dir


def command_build(args: argparse.Namespace, work_dir: Path | None = None) -> list[Path]:
    work_dir = work_dir or Path(args.work_dir)
    album = Album.load(work_dir)
    if args.title:
        album.title = args.title
    if args.author:
        album.author = args.author
    photos = [p for p in album.photos if p.file and (work_dir / p.file).exists()]
    if not photos:
        raise SystemExit(f"No downloaded images in {work_dir}; run fetch first.")
    chapters = group_chapters(photos, args.toc, "Ảnh" if args.lang == "vi" else "Photo")
    stem = safe_filename(album.title or album.id)
    out_dir = Path(args.output_dir) if args.output_dir else work_dir
    outputs = []
    for fmt in args.formats.split(","):
        fmt = fmt.strip().lower()
        target = out_dir / f"{stem}.{fmt}"
        if fmt == "epub":
            outputs.append(build_epub(album, work_dir, target, chapters, args.lang))
        elif fmt == "pdf":
            outputs.append(build_pdf(album, work_dir, target, chapters, args.lang, args.page_size, args.font))
        else:
            raise SystemExit(f"Unknown format {fmt!r} (use epub, pdf)")
        log(f"Built {target} — {len(chapters)} TOC entries, {len(photos)} photos")
    return outputs


def add_build_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--formats", default="epub", help="comma list: epub,pdf (default epub)")
    parser.add_argument("--toc", choices=["caption", "photo"], default="caption",
                        help="caption: captioned photo starts a chapter (default); photo: one entry per photo")
    parser.add_argument("--title", help="override the album title")
    parser.add_argument("--author", help="book author / page name")
    parser.add_argument("--lang", default="vi")
    parser.add_argument("--page-size", choices=["a5", "a4", "letter"], default="a5", help="PDF page size")
    parser.add_argument("--font", help="TTF font for PDF (must support Vietnamese)")
    parser.add_argument("--output-dir", help="where to write ebooks (default: work dir)")


def add_fetch_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("album", help="album URL / id; for --source export: export folder or album JSON")
    parser.add_argument("--source", choices=["browser", "graph", "export"], default="browser")
    parser.add_argument("--work-dir", help=f"default: {DEFAULT_WORK_ROOT}/<album id>")
    parser.add_argument("--token", help="Graph API access token (or FB_ACCESS_TOKEN)")
    parser.add_argument("--profile", default=str(DEFAULT_PROFILE_DIR), help="browser profile dir")
    parser.add_argument("--headless", action="store_true", help="run the browser without a window")
    parser.add_argument("--limit", type=int, default=0, help="only the first N photos (testing)")
    parser.add_argument("--delay", type=float, default=1.5, help="seconds between photo pages")
    parser.add_argument("--album-name", default="", help="export source: album name to pick")
    parser.add_argument("--max-image-side", type=int, default=1600,
                        help="downscale images to this many px (needs Pillow; 0 = keep)")


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    login = sub.add_parser("login", help="open a browser window to log in to Facebook once")
    login.add_argument("--profile", default=str(DEFAULT_PROFILE_DIR))

    fetch = sub.add_parser("fetch", help="download album metadata, captions and images")
    add_fetch_options(fetch)

    build = sub.add_parser("build", help="build ebook(s) from a fetched work dir")
    build.add_argument("work_dir")
    add_build_options(build)

    send = sub.add_parser("send", help="email an ebook to a Kindle address")
    send.add_argument("file")
    send.add_argument("--to", default=os.environ.get("KINDLE_EMAIL", ""), help="xxx@kindle.com (or KINDLE_EMAIL)")

    run = sub.add_parser("run", help="fetch + build (+ send with --send)")
    add_fetch_options(run)
    add_build_options(run)
    run.add_argument("--send", action="store_true", help="email the first built ebook to Kindle")
    run.add_argument("--to", default=os.environ.get("KINDLE_EMAIL", ""))

    args = parser.parse_args(list(argv) if argv is not None else None)
    if args.command == "login":
        browser_login(Path(args.profile))
    elif args.command == "fetch":
        command_fetch(args)
    elif args.command == "build":
        command_build(args)
    elif args.command == "send":
        if not args.to:
            raise SystemExit("Pass --to or set KINDLE_EMAIL")
        send_to_kindle(Path(args.file), args.to)
    elif args.command == "run":
        work_dir = command_fetch(args)
        outputs = command_build(args, work_dir)
        if args.send:
            if not args.to:
                raise SystemExit("Pass --to or set KINDLE_EMAIL")
            send_to_kindle(outputs[0], args.to)
    return 0


if __name__ == "__main__":
    sys.exit(main())
