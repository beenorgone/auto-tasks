import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.parse
import xml.dom.minidom
import zipfile
from pathlib import Path


SPEC = importlib.util.spec_from_file_location("fb_album_ebook", Path(__file__).with_name("fb-album-ebook.py"))
APP = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = APP  # dataclasses resolve annotations via sys.modules
SPEC.loader.exec_module(APP)

# 1x1 white JPEG
TINY_JPEG = bytes.fromhex(
    "ffd8ffe000104a46494600010100000100010000ffdb004300080606070605080707070909080a0c140d0c0b0b0c1912130f141d1a"
    "1f1e1d1a1c1c20242e2720222c231c1c2837292c30313434341f27393d38323c2e333432ffc0000b080001000101011100ffc4001f"
    "0000010501010101010100000000000000000102030405060708090a0bffc400b5100002010303020403050504040000017d010203"
    "00041105122131410613516107227114328191a1082342b1c11552d1f02433627282090a161718191a25262728292a3435363738"
    "393a434445464748494a535455565758595a636465666768696a737475767778797a838485868788898a92939495969798999aa2"
    "a3a4a5a6a7a8a9aab2b3b4b5b6b7b8b9bac2c3c4c5c6c7c8c9cad2d3d4d5d6d7d8d9dae1e2e3e4e5e6e7e8e9eaf1f2f3f4f5f6f7"
    "f8f9faffda0008010100003f00fbd3ffd9"
)


def make_album(work_dir: Path, captions: list[str]) -> "APP.Album":
    album = APP.Album(id="122143769745029493", title="Công thức Protein thực vật", author="IVAR")
    (work_dir / "images").mkdir(parents=True)
    for index, caption in enumerate(captions, start=1):
        rel = f"images/{index:04d}.jpg"
        (work_dir / rel).write_bytes(TINY_JPEG)
        album.photos.append(APP.Photo(id=str(index), caption=caption, file=rel))
    album.save(work_dir)
    return album


class ParseTests(unittest.TestCase):
    def test_album_id_from_common_urls(self):
        expected = "122143769745029493"
        for url in (
            "https://web.facebook.com/media/set/?set=a.122143769745029493&type=3",
            "https://www.facebook.com/somepage/photos/a.122143769745029493/99/",
            "https://m.facebook.com/media/set/?set=a.122143769745029493",
            "122143769745029493",
        ):
            self.assertEqual(expected, APP.parse_album_id(url), url)
        with self.assertRaises(ValueError):
            APP.parse_album_id("https://www.facebook.com/somepage")

    def test_fix_mojibake_repairs_export_text_only(self):
        broken = "Công thức".encode("utf-8").decode("latin-1")
        self.assertEqual("Công thức", APP.fix_mojibake(broken))
        self.assertEqual("Công thức", APP.fix_mojibake("Công thức"))

    def test_caption_grouping_attaches_uncaptioned_photos(self):
        photos = [APP.Photo("1", ""), APP.Photo("2", "Bài 1: Đạm đậu Hà Lan\nchi tiết"),
                  APP.Photo("3", ""), APP.Photo("4", "  \n#Bài 2  ")]
        chapters = APP.group_chapters(photos)
        self.assertEqual(["Ảnh 1", "Bài 1: Đạm đậu Hà Lan", "Bài 2"], [c.title for c in chapters])
        self.assertEqual(["2", "3"], [p.id for p in chapters[1].photos])
        self.assertEqual(4, len(APP.group_chapters(photos, mode="photo")))


class SourceTests(unittest.TestCase):
    def test_graph_follows_paging_and_picks_largest_image(self):
        pages = {
            "meta": {"name": "Album", "from": {"name": "Page"}, "link": "https://fb/album"},
            "p1": {"data": [{"id": "2", "name": "newest", "images": [
                {"width": 100, "source": "small"}, {"width": 2000, "source": "big"}]}],
                   "paging": {"next": "NEXT"}},
            "p2": {"data": [{"id": "1", "name": "oldest", "images": [{"width": 50, "source": "only"}]}]},
        }

        def fake_get(url):
            if url == "NEXT":
                return pages["p2"]
            return pages["p1"] if "/photos?" in url else pages["meta"]

        album = APP.fetch_graph("42", "token", get_json=fake_get)
        self.assertEqual(("Album", "Page"), (album.title, album.author))
        self.assertEqual(["1", "2"], [p.id for p in album.photos])
        self.assertEqual("big", album.photos[1].image_url)

    def test_export_album_json_resolves_images_and_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "export"
            media = root / "your_facebook_activity" / "posts" / "media" / "Album_1"
            media.mkdir(parents=True)
            (media / "a.jpg").write_bytes(TINY_JPEG)
            album_dir = root / "your_facebook_activity" / "posts" / "album"
            album_dir.mkdir(parents=True)
            (album_dir / "0.json").write_text(json.dumps({
                "name": "Đồ uống".encode("utf-8").decode("latin-1"),
                "photos": [{"uri": "your_facebook_activity/posts/media/Album_1/a.jpg",
                            "creation_timestamp": 1700000000, "description": "Mô tả"}],
            }), encoding="utf-8")
            album = APP.load_export(root)
            self.assertEqual("Đồ uống", album.title)
            work = Path(temporary) / "work"
            APP.download_images(album, work, 0, getter=APP.read_file_url)
            self.assertTrue((work / album.photos[0].file).exists())
            self.assertEqual("Mô tả", APP.Album.load(work).photos[0].caption)

    def test_bookmarklet_runs_merge_and_keep_downloaded_files(self):
        def export(photos, complete):
            return {"format": APP.BOOKMARKLET_FORMAT, "complete": complete,
                    "album": {"id": "999", "title": "Công thức", "source_url": "https://fb/set"},
                    "photos": [{"id": i, "caption": c, "created_time": "", "image_url": u} for i, c, u in photos]}

        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "part1.json"
            first.write_text(json.dumps(export([("1", "Bài 1", "u1"), ("2", "", "u2")], False)), encoding="utf-8")
            second = Path(temporary) / "part2.json"
            second.write_text(json.dumps(export([("2", "", "u2-fresh"), ("3", "Bài 2", "u3")], True)), encoding="utf-8")
            work = Path(temporary) / "work"

            album = APP.load_bookmarklet(first)
            APP.download_images(album, work, 0, getter=lambda url: TINY_JPEG)
            merged = APP.merge_album(APP.Album.load(work), APP.load_bookmarklet(second))
            self.assertEqual(["1", "2", "3"], [p.id for p in merged.photos])
            self.assertEqual("u2-fresh", merged.photos[1].image_url)
            self.assertEqual("images/0002.jpg", merged.photos[1].file)
            self.assertEqual("", merged.photos[2].file)

            first.write_text(json.dumps({"format": "other"}), encoding="utf-8")
            with self.assertRaises(SystemExit):
                APP.load_bookmarklet(first)

    def test_bookmarklet_url_inlines_shared_photo_reader(self):
        url = APP.bookmarklet_url()
        self.assertTrue(url.startswith("javascript:"))
        code = urllib.parse.unquote(url[len("javascript:"):])
        self.assertNotIn("__PHOTO_DETAIL_JS__", code)
        self.assertIn('data-visualcompletion="media-vc-image"', code)
        self.assertIn("DELAY_MIN = 20, DELAY_MAX = 60", code)


class BuildTests(unittest.TestCase):
    def test_epub_structure_and_toc(self):
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            album = make_album(work, ["Bài 1 <đạm>", "", "Bài 2 & kết"])
            chapters = APP.group_chapters(album.photos)
            output = APP.build_epub(album, work, work / "book.epub", chapters, "vi")
            with zipfile.ZipFile(output) as book:
                names = book.namelist()
                self.assertEqual("mimetype", names[0])
                self.assertEqual(zipfile.ZIP_STORED, book.infolist()[0].compress_type)
                for name in names:
                    if name.endswith((".xhtml", ".opf", ".ncx", ".xml")):
                        xml.dom.minidom.parseString(book.read(name))  # well-formed
                nav = book.read("OEBPS/nav.xhtml").decode()
                self.assertIn("Bài 1 &lt;đạm&gt;", nav)
                self.assertIn("Bài 2 &amp; kết", nav)
                self.assertIn('properties="cover-image"', book.read("OEBPS/content.opf").decode())
                self.assertEqual(3, len([n for n in names if n.startswith("OEBPS/images/")]))
                self.assertEqual(2, nav.count("<li>"))

    def test_pdf_builds_when_reportlab_available(self):
        try:
            import reportlab  # noqa: F401
        except ImportError:
            self.skipTest("reportlab not installed")
        with tempfile.TemporaryDirectory() as temporary:
            work = Path(temporary)
            album = make_album(work, ["Bài 1", "Bài 2"])
            output = APP.build_pdf(album, work, work / "book.pdf", APP.group_chapters(album.photos), "vi")
            data = output.read_bytes()
            self.assertTrue(data.startswith(b"%PDF"))
            self.assertIn(b"/Outlines", data)


class SendTests(unittest.TestCase):
    def test_kindle_message_attaches_epub(self):
        with tempfile.TemporaryDirectory() as temporary:
            book = Path(temporary) / "Album.epub"
            book.write_bytes(b"PK")
            message = APP.build_kindle_message(book, "me@kindle.com", "me@gmail.com")
            attachment = next(message.iter_attachments())
            self.assertEqual("application/epub+zip", attachment.get_content_type())
            self.assertEqual("Album.epub", attachment.get_filename())

    def test_send_requires_credentials(self):
        with tempfile.TemporaryDirectory() as temporary:
            book = Path(temporary) / "Album.epub"
            book.write_bytes(b"PK")
            with self.assertRaises(SystemExit):
                APP.send_to_kindle(book, "me@kindle.com", env={})


if __name__ == "__main__":
    unittest.main()
