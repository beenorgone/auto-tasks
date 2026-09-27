#!/usr/bin/env python3
"""Save a publicly readable Truyen Tuoi Tho chapter as a page-for-page PDF.

Stages: scan -> download (resumable) -> package. No login or access-control bypass.
"""

from __future__ import annotations

import argparse
import fcntl
import io
import json
import random
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, UnidentifiedImageError
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


SITE_HOST = "truyentuoitho.com"
IMAGE_HOSTS = {SITE_HOST, "img.resourcehub.shop"}
USER_AGENT = "MangaChapterPDF/1.0 (personal offline reader)"
MAX_PAGES = 500
MAX_CHAPTERS = 100
MAX_IMAGE_BYTES = 15 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 40_000_000


class AccessLimitedError(RuntimeError):
    """The server denied or rate-limited access; stop the whole batch."""


class PagesUnavailableError(ValueError):
    """The chapter page does not expose readable image elements."""


class PacedSession(requests.Session):
    def __init__(self, delay: float) -> None:
        super().__init__()
        self.delay = delay
        self.last_request = 0.0
        self.headers.update({"User-Agent": USER_AGENT})

    def get(self, url: str, **kwargs) -> requests.Response:
        if self.last_request:
            pause = self.delay * random.uniform(0.8, 1.2)
            time.sleep(max(0, self.last_request + pause - time.monotonic()))
        self.last_request = time.monotonic()
        return super().get(url, **kwargs)


@dataclass(frozen=True)
class Chapter:
    url: str
    title: str
    image_urls: list[str]


def allowed_url(url: str, hosts: set[str]) -> bool:
    parts = urlparse(url)
    return parts.scheme == "https" and parts.hostname in hosts and not parts.username and not parts.password


def valid_chapter_url(url: str) -> bool:
    parts = urlparse(url)
    return (allowed_url(url, {SITE_HOST})
            and bool(re.fullmatch(r"/manga/[a-zA-Z0-9-]+/[a-zA-Z0-9-]+/?", parts.path))
            and not parts.query and not parts.fragment)


def parse_chapter(html: str, url: str) -> Chapter:
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.select_one("#chapter-heading")
    images = soup.select(".reading-content #comic-wrap .page-box img")
    if not heading or not images:
        raise PagesUnavailableError("Không thấy ảnh trong HTML; tập có thể yêu cầu quyền truy cập.")
    title = heading.get_text(" ", strip=True)
    urls = []
    for image in images:
        raw = image.get("data-src") or image.get("src") or ""
        image_url = urljoin(url, raw.strip())
        if not allowed_url(image_url, IMAGE_HOSTS):
            raise ValueError(f"Máy chủ ảnh không được hỗ trợ: {urlparse(image_url).hostname}")
        urls.append(image_url)
    if len(urls) > MAX_PAGES or len(set(urls)) != len(urls):
        raise ValueError("Danh sách ảnh quá dài hoặc trùng lặp; dừng để tránh tạo PDF sai.")
    return Chapter(url=url, title=title, image_urls=urls)


def get(session: requests.Session, url: str, *, referer: str, timeout: int = 30) -> requests.Response:
    response = session.get(url, headers={"Referer": referer}, timeout=timeout,
                           allow_redirects=False, stream=True)
    if response.status_code in (301, 302, 303, 307, 308):
        response.close()
        raise ValueError("Trang chuyển hướng; dừng để tránh tải từ máy chủ khác.")
    if response.status_code in (403, 429):
        retry_after = response.headers.get("Retry-After", "")
        response.close()
        note = f" Retry-After: {retry_after}." if retry_after else ""
        raise AccessLimitedError(f"Website trả {response.status_code}; dừng toàn bộ đợt tải.{note}")
    response.raise_for_status()
    return response


def scan(session: requests.Session, url: str) -> Chapter:
    parts = urlparse(url)
    if not valid_chapter_url(url):
        raise ValueError("Cần URL HTTPS của một tập truyện trên truyentuoitho.com.")
    with get(session, url, referer=f"https://{SITE_HOST}/") as response:
        if "text/html" not in response.headers.get("Content-Type", "").lower():
            raise ValueError("URL không trả về HTML.")
        response.encoding = "utf-8"
        return parse_chapter(response.text, url)


def discover_series(session: requests.Session, url: str) -> list[str]:
    parts = urlparse(url)
    if (not allowed_url(url, {SITE_HOST})
            or not re.fullmatch(r"/manga/[a-zA-Z0-9-]+/?", parts.path)
            or parts.query):
        raise ValueError("Cần URL HTTPS của trang bộ truyện trên truyentuoitho.com.")
    base = url.rstrip("/") + "/"
    endpoint = base + "ajax/chapters/?t=1"
    response = session.post(endpoint, headers={"Referer": base}, timeout=30,
                            allow_redirects=False)
    if response.status_code in (403, 429):
        raise AccessLimitedError(f"Website trả {response.status_code}; dừng lấy danh sách.")
    if response.is_redirect:
        raise ValueError("Danh sách tập chuyển hướng; dừng để tránh URL ngoài website.")
    response.raise_for_status()
    response.encoding = "utf-8"
    soup = BeautifulSoup(response.text, "html.parser")
    urls = []
    slug = parts.path.strip("/").split("/")[-1]
    for link in soup.select("li.wp-manga-chapter a[href]"):
        chapter_url = urljoin(base, link["href"])
        chapter_parts = urlparse(chapter_url)
        if (not allowed_url(chapter_url, {SITE_HOST})
                or not re.fullmatch(rf"/manga/{re.escape(slug)}/[a-zA-Z0-9-]+/?", chapter_parts.path)
                or chapter_parts.query):
            raise ValueError(f"URL tập không hợp lệ trong danh sách: {chapter_url}")
        urls.append(chapter_url)
    urls = list(dict.fromkeys(urls))
    if not urls or len(urls) > MAX_CHAPTERS or soup.select_one(".pagination a"):
        raise ValueError("Danh sách tập trống, quá dài hoặc có nhiều trang; dừng để tránh thiếu tập.")
    return list(reversed(urls))


def save_url_list(urls: list[str], output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_text("".join(url + "\n" for url in urls), encoding="utf-8")
    temporary.replace(output)


def queue_state_path(urls: list[str], output_dir: Path) -> Path:
    slug = urlparse(urls[0]).path.strip("/").split("/")[-2]
    return output_dir / f"{slug}-queue-state.json"


def read_queue_state(path: Path) -> dict:
    if not path.exists():
        return {"blocked": {}}
    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state.get("blocked"), dict):
        raise ValueError("Trạng thái hàng đợi không hợp lệ.")
    return state


def save_queue_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def save_manifest(chapter: Chapter, folder: Path) -> None:
    target = folder / "chapter.json"
    payload = {"url": chapter.url, "title": chapter.title, "image_urls": chapter.image_urls}
    if target.exists():
        old = json.loads(target.read_text(encoding="utf-8"))
        if old != payload:
            raise ValueError("Danh sách trang đã thay đổi. Hãy chọn thư mục đầu ra khác để tránh trộn hai bản.")
        return
    folder.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)


def valid_image(path: Path) -> bool:
    try:
        if not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
            return False
        with Image.open(path) as image:
            image.verify()
        return True
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombError):
        return False


def download_page(session: requests.Session, url: str, target: Path, referer: str) -> None:
    for attempt in range(3):
        temporary = target.with_suffix(".webp.part")
        try:
            with get(session, url, referer=referer) as response:
                if not response.headers.get("Content-Type", "").lower().startswith("image/"):
                    raise ValueError(f"Trang ảnh trả về {response.headers.get('Content-Type', 'unknown')}")
                size = 0
                with temporary.open("wb") as output:
                    for chunk in response.iter_content(chunk_size=65536):
                        size += len(chunk)
                        if size > MAX_IMAGE_BYTES:
                            raise ValueError("Ảnh vượt giới hạn 15 MB")
                        output.write(chunk)
            if not valid_image(temporary):
                raise ValueError("Dữ liệu ảnh không hợp lệ")
            temporary.replace(target)
            return
        except (requests.RequestException, OSError, ValueError) as error:
            temporary.unlink(missing_ok=True)
            if attempt == 2 or isinstance(error, ValueError):
                raise RuntimeError(f"Tải ảnh thất bại: {url} ({error})") from error
            time.sleep(2 ** attempt)


def download(chapter: Chapter, folder: Path, session: requests.Session) -> list[Path]:
    images = folder / "images"
    images.mkdir(parents=True, exist_ok=True)
    paths = []
    total = len(chapter.image_urls)
    for number, url in enumerate(chapter.image_urls, 1):
        path = images / f"{number:04d}.webp"
        if not valid_image(path):
            download_page(session, url, path, chapter.url)
            print(f"[Tải ảnh {number}/{total}] {path.name}", flush=True)
        else:
            print(f"[Đã có {number}/{total}] {path.name}", flush=True)
        paths.append(path)
    return paths


def read_urls(positional: list[str], file: Path | None) -> list[str]:
    urls = positional[:]
    if file:
        urls.extend(line.strip() for line in file.read_text(encoding="utf-8-sig").splitlines()
                    if line.strip() and not line.lstrip().startswith("#"))
    urls = list(dict.fromkeys(urls))
    if not urls or len(urls) > MAX_CHAPTERS:
        raise ValueError(f"Cần 1-{MAX_CHAPTERS} URL tập truyện.")
    if not all(valid_chapter_url(url) for url in urls):
        raise ValueError("Danh sách có URL tập không hợp lệ.")
    return urls


def chapter_files(url: str, output_dir: Path) -> tuple[Path, Path]:
    parts = urlparse(url).path.strip("/").split("/")
    folder = output_dir / parts[-2] / parts[-1]
    return folder / "chapter.json", folder / f"{parts[-2]}-{parts[-1]}.pdf"


def is_chapter_complete(url: str, output_dir: Path) -> bool:
    manifest, pdf = chapter_files(url, output_dir)
    if not manifest.exists():
        return False
    data = json.loads(manifest.read_text(encoding="utf-8"))
    return complete_pdf(pdf, len(data["image_urls"]))


def show_status(urls: list[str], output_dir: Path) -> None:
    if not urls or not all(valid_chapter_url(url) for url in urls):
        raise ValueError("Danh sách có URL tập không hợp lệ.")
    blocked = read_queue_state(queue_state_path(urls, output_dir))["blocked"]
    for number, url in enumerate(urls, 1):
        parts = urlparse(url).path.strip("/").split("/")
        folder = output_dir / parts[-2] / parts[-1]
        manifest, pdf = chapter_files(url, output_dir)
        if not manifest.exists():
            if url in blocked:
                print(f"[{number}/{len(urls)}] {parts[-1]}: Cần xem lại → {blocked[url]}")
            else:
                print(f"[{number}/{len(urls)}] {parts[-1]}: Chưa bắt đầu → tiếp theo: tải")
            continue
        data = json.loads(manifest.read_text(encoding="utf-8"))
        total = len(data["image_urls"])
        present = sum(1 for path in (folder / "images").glob("*.webp") if path.is_file())
        if complete_pdf(pdf, total):
            state, next_step = "Hoàn tất", str(pdf)
        elif url in blocked:
            state, next_step = "Cần xem lại", blocked[url]
        elif present == total:
            state, next_step = "Đủ ảnh", "đóng PDF"
        else:
            state, next_step = "Đang tải", "tiếp tục tải"
        print(f"[{number}/{len(urls)}] {parts[-1]}: {state} ({present}/{total}) → {next_step}")


def run_queue(urls: list[str], output_dir: Path, max_new: int, retry_blocked: bool,
              session: requests.Session, chapter_delay: float) -> int:
    if not urls or not all(valid_chapter_url(url) for url in urls):
        raise ValueError("Hàng đợi có URL không hợp lệ.")
    state_path = queue_state_path(urls, output_dir)
    lock_path = state_path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("Hàng đợi này đang chạy; không mở lượt tải thứ hai.") from None
        state = read_queue_state(state_path)
        if retry_blocked:
            state["blocked"] = {}
            save_queue_state(state_path, state)
        attempted = completed = 0
        for number, url in enumerate(urls, 1):
            if is_chapter_complete(url, output_dir):
                continue
            if url in state["blocked"]:
                continue
            if attempted >= max_new:
                break
            if attempted:
                time.sleep(chapter_delay)
            attempted += 1
            print(f"[Hàng đợi {number}/{len(urls)}] {url}", flush=True)
            try:
                process_chapter(session, url, output_dir)
                completed += 1
            except PagesUnavailableError as error:
                state["blocked"][url] = str(error)
                save_queue_state(state_path, state)
                print(f"[Cần xem lại] {url}: {error}", flush=True)
        remaining = len(urls) - sum(is_chapter_complete(url, output_dir) for url in urls)
        print(f"[Tổng kết] Mới hoàn tất: {completed}; còn chưa hoàn tất: {remaining};"
              f" cần xem lại: {len(state['blocked'])}.", flush=True)
        return 0


def process_chapter(session: requests.Session, url: str, output_dir: Path) -> Path:
    chapter = scan(session, url)
    print(f"[Kiểm tra] {chapter.title}: {len(chapter.image_urls)} trang", flush=True)
    parts = urlparse(chapter.url).path.strip("/").split("/")
    folder = output_dir / parts[-2] / parts[-1]
    save_manifest(chapter, folder)
    output = folder / f"{parts[-2]}-{parts[-1]}.pdf"
    if complete_pdf(output, len(chapter.image_urls)):
        print(f"[Đã hoàn tất] {output} ({len(chapter.image_urls)} trang)", flush=True)
        return output
    paths = download(chapter, folder, session)
    print("[Đóng gói] Đã tải đủ ảnh; đang tạo PDF.", flush=True)
    build_pdf(chapter, paths, output)
    print(f"[Hoàn tất] {output} ({len(paths)} trang)", flush=True)
    return output


def build_pdf(chapter: Chapter, paths: list[Path], output: Path) -> None:
    if len(paths) != len(chapter.image_urls) or not all(valid_image(path) for path in paths):
        raise ValueError("Chưa đủ ảnh hợp lệ; không tạo PDF thiếu trang.")
    temporary = output.with_suffix(".pdf.part")
    try:
        pdf = canvas.Canvas(str(temporary), pageCompression=1)
        pdf.setTitle(chapter.title)
        pdf.setAuthor("Truyen Tuoi Tho")
        pdf.setSubject(chapter.url)
        for number, path in enumerate(paths, 1):
            with Image.open(path) as image:
                image.load()
                rgb = image.convert("RGB")
                width, height = rgb.size
                scale = min(612 / width, 792 / height)
                page_width, page_height = width * scale, height * scale
                data = io.BytesIO()
                rgb.save(data, format="JPEG", quality=80, optimize=True)
                data.seek(0)
                pdf.setPageSize((page_width, page_height))
                pdf.drawImage(ImageReader(data), 0, 0, page_width, page_height)
                pdf.showPage()
            print(f"[Đóng PDF {number}/{len(paths)}]", flush=True)
        pdf.save()
        if temporary.stat().st_size < 1024:
            raise ValueError("PDF quá nhỏ, có thể bị lỗi.")
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def complete_pdf(output: Path, expected_pages: int) -> bool:
    if not output.is_file() or not shutil.which("pdfinfo"):
        return False
    result = subprocess.run(["pdfinfo", str(output)], capture_output=True, text=True, check=False)
    if result.returncode:
        return False
    match = re.search(r"^Pages:\s+(\d+)$", result.stdout, flags=re.MULTILINE)
    return bool(match and int(match.group(1)) == expected_pages)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("discover", "scan", "run", "bulk", "queue", "status"))
    parser.add_argument("urls", nargs="*", help="URL của một hoặc nhiều tập truyện")
    parser.add_argument("--file", type=Path, help="File UTF-8, một URL mỗi dòng (bulk)")
    parser.add_argument("--list-output", type=Path, help="File danh sách đầu ra (discover)")
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parent / "manga-ebooks")
    parser.add_argument("--delay", type=float, default=2.0,
                        help="Giãn cách giữa các request, dao động ±20%% (mặc định: 2 giây)")
    parser.add_argument("--chapter-delay", type=float, default=5.0,
                        help="Giây nghỉ thêm giữa các tập trong bulk (mặc định: 5)")
    parser.add_argument("--max-new", type=int, default=5,
                        help="Số tập tối đa xử lý trong một lượt queue (mặc định: 5)")
    parser.add_argument("--retry-blocked", action="store_true",
                        help="Thử lại các tập đã đánh dấu cần xem lại (queue)")
    args = parser.parse_args(argv)
    if args.delay < 0.5:
        parser.error("--delay phải từ 0.5 giây trở lên")
    if args.chapter_delay < 0:
        parser.error("--chapter-delay không được âm")
    if not 1 <= args.max_new <= 20:
        parser.error("--max-new phải trong khoảng 1-20")
    if args.command in ("discover", "scan", "run") and (args.file or len(args.urls) != 1):
        parser.error("discover/scan/run cần đúng một URL và không dùng --file")
    if args.list_output and args.command != "discover":
        parser.error("--list-output chỉ dùng với discover")
    if args.command in ("bulk", "queue", "status") and not (args.urls or args.file):
        parser.error("bulk/queue/status cần URL hoặc --file")
    if args.retry_blocked and args.command != "queue":
        parser.error("--retry-blocked chỉ dùng với queue")
    if args.command == "status":
        try:
            show_status(read_urls(args.urls, args.file), args.output_dir)
            return 0
        except (OSError, ValueError, KeyError) as error:
            print(f"[Lỗi] {error}", file=sys.stderr)
            return 1
    session = PacedSession(args.delay)
    try:
        if args.command == "discover":
            urls = discover_series(session, args.urls[0])
            slug = urlparse(args.urls[0]).path.strip("/").split("/")[-1]
            output = args.list_output or args.output_dir / f"{slug}-links.txt"
            save_url_list(urls, output)
            print(f"[Danh sách] {len(urls)} tập: {output}")
            print(f"[Đầu → cuối] {urls[0]} → {urls[-1]}")
            print("Tiếp theo: kiểm tra quyền truy cập từng tập trước khi chạy bulk.")
            return 0
        if args.command == "scan":
            chapter = scan(session, args.urls[0])
            print(f"[Kiểm tra] {chapter.title}: {len(chapter.image_urls)} trang", flush=True)
            print("Tiếp theo: chạy lệnh run với cùng URL để tải ảnh và tạo PDF.")
            return 0
        urls = read_urls(args.urls, args.file)
        if args.command == "run":
            process_chapter(session, urls[0], args.output_dir)
            return 0
        if args.command == "queue":
            return run_queue(urls, args.output_dir, args.max_new, args.retry_blocked,
                             session, args.chapter_delay)
        completed = 0
        failed = 0
        for number, url in enumerate(urls, 1):
            if number > 1:
                time.sleep(args.chapter_delay)
            print(f"[Tập {number}/{len(urls)}] {url}", flush=True)
            try:
                process_chapter(session, url, args.output_dir)
                completed += 1
            except AccessLimitedError:
                raise
            except (requests.RequestException, OSError, RuntimeError, ValueError) as error:
                failed += 1
                print(f"[Lỗi tập {number}] {error}", file=sys.stderr, flush=True)
        print(f"[Tổng kết] Hoàn tất: {completed}; lỗi: {failed}."
              " Tiếp theo: chạy lại bulk để tiếp tục các tập lỗi.", flush=True)
        return 1 if failed else 0
    except (requests.RequestException, OSError, RuntimeError, ValueError) as error:
        print(f"[Lỗi] {error}", file=sys.stderr)
        print("Tiếp theo: kiểm tra lỗi rồi chạy lại lệnh run để tiếp tục từ ảnh đã tải.", file=sys.stderr)
        return 1
    finally:
        session.close()


if __name__ == "__main__":
    raise SystemExit(main())
