from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote as url_quote, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .analyzer import _text_dep_hints
from .config import DOWNLOADS_DIR, resource_path
from .utils import ProgressCB, human_size, noop_progress, sanitize_segment, unique_destination

BASE_URL = "https://www.gta5-mods.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
ARCHIVE_MAGIC = {
    ".zip": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".oiv": (b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"),
    ".rar": (b"Rar!",),
    ".7z": (b"7z\xbc\xaf\x27\x1c",),
}
GITHUB_API = "https://api.github.com/repos/{repo}/releases"


class SiteError(Exception):
    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class DownloadCancelled(SiteError):
    pass


class ManualDependencyError(SiteError):
    pass


@dataclass
class ModListing:
    title: str
    url: str
    author: str = ""
    version: str = ""
    image: str = ""
    tag: str = ""
    downloads: int = 0
    rating: str = ""
    summary: str = ""
    deps: list[str] = field(default_factory=list)
    origin: str = "site"


@dataclass
class ModDetails:
    title: str
    page_url: str
    author: str = ""
    version: str = ""
    description: str = ""
    image: str = ""
    tags: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    download_href: str = ""
    deps: list[str] = field(default_factory=list)


@dataclass
class DownloadInfo:
    url: str
    filename: str
    referer: str = ""
    page_url: str = ""


@dataclass
class DownloadedFile:
    path: Path
    url: str
    filename: str
    size: int
    referer: str = ""

    @property
    def size_label(self) -> str:
        return human_size(self.size)


def _clean_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", value or "").strip()


def _parse_int(value: str) -> int:
    digits = re.sub(r"[^\d]", "", value or "")
    return int(digits) if digits else 0


def _absolute(href: str | None, base: str = BASE_URL) -> str:
    if not href:
        return ""
    return urljoin(base, href.strip())


def _host_allowed(host: str) -> bool:
    host = host.lower()
    return (
        host == "gta5-mods.com"
        or host.endswith(".gta5-mods.com")
        or host in {"github.com", "objects.githubusercontent.com", "dev-c.com"}
        or host.endswith(".dev-c.com")
    )


def _safe_remote_url(url: str, allowed: tuple[str, ...] = ()) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        raise SiteError("Недопустимая ссылка на скачивание.")
    host = parsed.hostname or ""
    if allowed:
        if not any(host == item or host.endswith("." + item) for item in allowed):
            raise SiteError(
                f"Ссылка ведёт на неподдерживаемый сайт: {host}.",
                "Скачайте файл вручную и перетащите его в установщик.",
            )
    elif not _host_allowed(host):
        raise SiteError(f"Ссылка ведёт на неподдерживаемый сайт: {host}.")
    return url


def _card_from_element(element, base: str = BASE_URL) -> ModListing | None:
    name_link = element.select_one(".details .name a") or element.select_one(".name a")
    if name_link is None:
        return None
    title = _clean_text(name_link.get_text(" "))
    href = name_link.get("href") or ""
    if not title or not href:
        return None
    author_link = element.select_one(".details .bottom a")
    image = element.select_one("a.preview img")
    version_el = element.select_one(".details .version")
    categories = [
        _clean_text(item.get_text(" ")).lower()
        for item in element.select("ul.categories li")
        if _clean_text(item.get_text(" "))
    ]
    rating_el = element.select_one("[title*='star rating']")
    downloads_el = element.select_one("[title*='Download']")
    return ModListing(
        title=title,
        url=_absolute(href, base),
        author=_clean_text(author_link.get_text(" ")) if author_link else "",
        version=_clean_text(version_el.get_text(" ")) if version_el else "",
        image=_absolute(image.get("src")) if image and image.get("src") else "",
        tag=", ".join(categories[:2]),
        downloads=_parse_int(downloads_el.get("title", "")) if downloads_el else 0,
        rating=_clean_text(rating_el.get("title", "")).replace(" star rating", "") if rating_el else "",
        origin="site",
    )


def load_catalog() -> list[ModListing]:
    path = resource_path("data", "catalog.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    items: list[ModListing] = []
    for raw in data.get("popular", []):
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        url = str(raw.get("url") or "").strip()
        if not title or not url.startswith("https://"):
            continue
        items.append(
            ModListing(
                title=title,
                url=url,
                author=str(raw.get("author") or ""),
                image=str(raw.get("image") or ""),
                tag=str(raw.get("tag") or ""),
                summary=str(raw.get("summary") or ""),
                deps=[str(key) for key in raw.get("deps") or []],
                origin="curated",
            )
        )
    return items


class SiteClient:
    def __init__(self) -> None:
        self.session = requests.Session()
        retry = Retry(total=2, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504))
        adapter = HTTPAdapter(max_retries=retry, pool_connections=8, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9,ru;q=0.8",
            }
        )

    def close(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def _get(self, url: str, *, timeout: int = 30, referer: str = "", stream: bool = False):
        headers = {"Referer": referer} if referer else {}
        try:
            response = self.session.get(url, headers=headers, timeout=timeout, stream=stream)
        except requests.exceptions.SSLError as exc:
            raise SiteError("Ошибка защищённого соединения.", str(exc)) from exc
        except requests.exceptions.Timeout as exc:
            raise SiteError("Сайт не ответил вовремя.", "Проверьте интернет и повторите попытку.") from exc
        except requests.exceptions.RequestException as exc:
            raise SiteError("Не удалось подключиться к сайту.", str(exc)) from exc
        if response.status_code == 404:
            response.close()
            raise SiteError("Страница не найдена.", "Возможно, мод удалён с сайта.")
        if response.status_code >= 400:
            response.close()
            raise SiteError(f"Сайт вернул ошибку {response.status_code}.", "Попробуйте позже.")
        return response

    def list_mods(self, sort: str = "popular", page: int = 1) -> list[ModListing]:
        path = "most-downloaded" if sort == "popular" else "latest"
        url = f"{BASE_URL}/all/{path}"
        if page > 1:
            url += f"?page={page}"
        response = self._get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[ModListing] = []
        for element in soup.select(".file-list-obj"):
            item = _card_from_element(element)
            if item is not None:
                items.append(item)
        return items

    def search_mods(self, query: str, page: int = 1) -> list[ModListing]:
        query = query.strip()
        if not query:
            return []
        url = f"{BASE_URL}/search/{url_quote(query, safe='')}"
        if page > 1:
            url += f"?page={page}"
        response = self._get(url)
        soup = BeautifulSoup(response.text, "html.parser")
        items: list[ModListing] = []
        for element in soup.select(".file-list-obj"):
            item = _card_from_element(element)
            if item is not None:
                items.append(item)
        return items

    def fetch_mod(self, page_url: str) -> ModDetails:
        page_url = _absolute(page_url)
        if not _host_allowed(urlparse(page_url).hostname or ""):
            raise SiteError("Поддерживаются только страницы GTA5-Mods.com.")
        response = self._get(page_url)
        soup = BeautifulSoup(response.text, "html.parser")
        root = soup.select_one("#file") or soup
        h1 = root.select_one("h1")
        version = ""
        if h1 is not None:
            version_el = h1.select_one(".version")
            if version_el is not None:
                version = _clean_text(version_el.get_text(" "))
                version_el.extract()
        title = _clean_text(h1.get_text(" ")) if h1 is not None else ""
        if not title and soup.title is not None:
            title = re.sub(r"\s*-\s*GTA5-Mods\.com.*$", "", soup.title.get_text(" "), flags=re.I)
        if not title:
            raise SiteError("Не удалось разобрать страницу мода.")
        author = ""
        author_link = root.select_one(".user-panel a.username") or root.select_one(".user-panel a[href^='/users/']")
        if author_link is not None:
            author = _clean_text(author_link.get_text(" "))
        if not author:
            for script in soup.select("script[type='application/ld+json']"):
                try:
                    data = json.loads(script.string or "")
                except (ValueError, TypeError):
                    continue
                if isinstance(data, dict) and data.get("@type") == "CreativeWork":
                    if isinstance(data.get("author"), str):
                        author = data["author"]
                        break
        descriptions: list[str] = []
        for element in root.select(".file-description, #description, .description"):
            text = _clean_text(element.get_text(" "))
            if len(text) > 30 and text not in descriptions:
                descriptions.append(text)
        description = max(descriptions, key=len) if descriptions else ""
        if not description:
            meta = soup.select_one("meta[name='description']")
            if meta is not None:
                description = _clean_text(meta.get("content", ""))
        image = ""
        og = soup.select_one("meta[property='og:image']")
        if og is not None:
            image = _absolute(og.get("content", ""))
        if not image:
            for img in root.select("img[src]"):
                src = img.get("src") or ""
                if src.startswith("http") and "/avatars/" not in src and "paypal" not in src.lower():
                    image = _absolute(src)
                    break
        slug = page_url.rstrip("/").split("/")[-1].lower()
        screenshots: list[str] = []
        for img in root.select("img[src]"):
            src = img.get("src") or ""
            if not src.startswith("http"):
                continue
            low = src.lower()
            if "/images/" not in low or "/avatars/" in low:
                continue
            if slug and slug not in low:
                continue
            if image and src.split("?")[0] == image.split("?")[0]:
                continue
            if src not in screenshots:
                screenshots.append(src)
            if len(screenshots) >= 6:
                break
        tags: list[str] = []
        for link in root.select("a[href*='/all/tags/']"):
            text = _clean_text(link.get_text(" "))
            if text and text not in tags:
                tags.append(text)
            if len(tags) >= 8:
                break
        download_link = root.select_one("a.btn-download")
        download_href = _absolute(download_link.get("href"), page_url) if download_link is not None else ""
        deps = sorted(_text_dep_hints(title + " " + description))
        return ModDetails(
            title=title,
            page_url=page_url,
            author=author,
            version=version,
            description=description,
            image=image,
            tags=tags,
            screenshots=screenshots,
            download_href=download_href,
            deps=deps,
        )

    def resolve_download(self, details: ModDetails) -> DownloadInfo:
        if not details.download_href:
            raise SiteError(
                "У мода нет файла для скачивания.",
                "Возможно, автор удалил файл или он доступен только вручную.",
            )
        _safe_remote_url(details.download_href, ("gta5-mods.com",))
        self._get(details.page_url, timeout=30)
        response = self._get(details.download_href, referer=details.page_url, timeout=30)
        if "attachment" in (response.headers.get("Content-Disposition") or "").lower():
            final_url = response.url
            filename = self._filename_from_headers(response, final_url)
            response.close()
            return DownloadInfo(final_url, filename, details.page_url, details.page_url)
        soup = BeautifulSoup(response.text, "html.parser")
        container = soup.select_one("#file-download") or soup
        link = container.select_one("a.btn-download[href]")
        if link is None:
            for candidate in container.select("a[href]"):
                href = candidate.get("href") or ""
                if ".zip" in href.lower() or ".rar" in href.lower() or ".7z" in href.lower():
                    link = candidate
                    break
        if link is None:
            raise SiteError(
                "Не удалось получить прямую ссылку на файл.",
                "Откройте страницу мода в браузере и скачайте файл вручную.",
            )
        url = _absolute(link.get("href"), details.download_href)
        url = _safe_remote_url(url, ("gta5-mods.com", "github.com"))
        filename = self._filename_from_headers(response, url)
        response.close()
        return DownloadInfo(url, filename, details.download_href, details.page_url)

    def _filename_from_headers(self, response, url: str) -> str:
        disposition = response.headers.get("Content-Disposition") or ""
        match = re.search(r"filename\*?\s*=\s*(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
        name = unquote(match.group(1)).strip() if match else ""
        if not name:
            name = unquote(Path(urlparse(url).path).name)
        name = sanitize_segment(name) if name else "mod_download"
        content_type = (response.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if not Path(name).suffix:
            if "zip" in content_type:
                name += ".zip"
            elif "rar" in content_type:
                name += ".rar"
            elif "7z" in content_type:
                name += ".7z"
        return name

    def download_file(
        self,
        info: DownloadInfo,
        dest_dir: Path | None = None,
        *,
        progress: ProgressCB = noop_progress,
        cancel=None,
        filename: str | None = None,
    ) -> DownloadedFile:
        _safe_remote_url(info.url)
        target_dir = Path(dest_dir or DOWNLOADS_DIR)
        target_dir.mkdir(parents=True, exist_ok=True)
        name = sanitize_segment(filename or info.filename or "mod_download")
        destination = unique_destination(target_dir, name)
        partial = destination.with_name(destination.name + ".part")
        headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
        if info.referer:
            headers["Referer"] = info.referer
        downloaded = 0
        total = 0
        try:
            with self.session.get(info.url, headers=headers, stream=True, timeout=(20, 90)) as response:
                if response.status_code >= 400:
                    raise SiteError(
                        f"Сервер отклонил скачивание (код {response.status_code}).",
                        "Попробуйте позже или скачайте файл вручную.",
                    )
                content_type = (response.headers.get("Content-Type") or "").lower()
                if "text/html" in content_type:
                    raise SiteError(
                        "Сервер вернул веб-страницу вместо файла.",
                        "GTA5-Mods мог запросить подтверждение. Повторите загрузку.",
                    )
                try:
                    total = int(response.headers.get("Content-Length") or 0)
                except ValueError:
                    total = 0
                disposition = response.headers.get("Content-Disposition") or ""
                if "filename" in disposition.lower():
                    header_name = self._filename_from_headers(response, info.url)
                    if header_name and header_name != name:
                        destination = unique_destination(target_dir, sanitize_segment(header_name))
                        partial = destination.with_name(destination.name + ".part")
                progress(0, f"Скачивание: {destination.name}")
                with open(partial, "wb") as handle:
                    for chunk in response.iter_content(chunk_size=512 * 1024):
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelled("Скачивание отменено.")
                        if not chunk:
                            continue
                        handle.write(chunk)
                        downloaded += len(chunk)
                        if total:
                            percent = min(99, int(downloaded * 100 / total))
                            note = f"Скачивание: {destination.name} · {human_size(downloaded)} из {human_size(total)}"
                        else:
                            percent = None
                            note = f"Скачивание: {destination.name} · {human_size(downloaded)}"
                        progress(percent, note)
            if downloaded <= 0:
                raise SiteError("Загруженный файл пуст.")
            magic_issue = _check_magic(partial, destination.suffix.lower())
            if magic_issue:
                raise SiteError(
                    f"Файл повреждён или это не архив: {destination.name}.",
                    magic_issue,
                )
            os.replace(partial, destination)
            progress(100, f"Скачано: {destination.name}")
            return DownloadedFile(
                path=destination,
                url=info.url,
                filename=destination.name,
                size=downloaded,
                referer=info.referer,
            )
        except DownloadCancelled:
            _unlink(partial)
            raise
        except requests.exceptions.Timeout as exc:
            _unlink(partial)
            raise SiteError("Скачивание прервано: сервер не отвечает.", str(exc)) from exc
        except requests.exceptions.RequestException as exc:
            _unlink(partial)
            raise SiteError("Ошибка сети при скачивании.", str(exc)) from exc
        except OSError as exc:
            _unlink(partial)
            raise SiteError("Не удалось сохранить файл.", str(exc)) from exc

    def download_mod(
        self,
        details: ModDetails,
        *,
        progress: ProgressCB = noop_progress,
        cancel=None,
    ) -> DownloadedFile:
        info = self.resolve_download(details)
        return self.download_file(info, progress=progress, cancel=cancel)

    def download_github_dependency(
        self,
        key: str,
        *,
        progress: ProgressCB = noop_progress,
        cancel=None,
        dest_dir: Path | None = None,
    ) -> tuple[DownloadedFile, str]:
        spec = GITHUB_DEPS.get(key)
        if spec is None:
            raise SiteError(f"Неизвестная зависимость: {key}")
        progress(None, f"Поиск файла: {spec['title']}")
        asset = self._github_asset(spec["repo"], spec["preferred_tags"], spec.get("asset_patterns", ()))
        info = DownloadInfo(asset["url"], asset["name"], spec["page"])
        downloaded = self.download_file(
            info,
            dest_dir=dest_dir or (DOWNLOADS_DIR / "deps" / key),
            progress=progress,
            cancel=cancel,
        )
        return downloaded, str(asset.get("tag") or "")

    def _github_asset(self, repo: str, preferred_tags: tuple[str, ...], patterns: tuple[str, ...]) -> dict:
        headers = {
            "User-Agent": "GTAModInstaller/1.0",
            "Accept": "application/vnd.github+json",
        }
        data = None
        try:
            response = self.session.get(
                GITHUB_API.format(repo=repo),
                headers=headers,
                params={"per_page": 10},
                timeout=25,
            )
            if response.status_code == 200:
                data = response.json()
            elif response.status_code == 403:
                raise SiteError(
                    "GitHub временно ограничил запросы.",
                    "Подождите несколько минут или установите компонент вручную.",
                )
        except (requests.exceptions.RequestException, ValueError):
            data = None
        if isinstance(data, list):
            for release in data:
                if not isinstance(release, dict) or release.get("prerelease") or release.get("draft"):
                    continue
                assets = [asset for asset in release.get("assets") or [] if isinstance(asset, dict)]
                chosen = _choose_github_asset(assets, patterns)
                if chosen:
                    return {
                        "name": chosen.get("name") or "dependency.zip",
                        "url": chosen.get("browser_download_url") or "",
                        "tag": release.get("tag_name") or "",
                    }
        pinned = GITHUB_PINNED.get(repo)
        if pinned:
            return {"name": pinned[1], "url": pinned[0], "tag": preferred_tags[0] if preferred_tags else ""}
        raise SiteError(
            "Не удалось найти файл зависимости на GitHub.",
            "Откройте страницу релиза и установите компонент вручную.",
        )

    def resolve_scripthookv(self) -> tuple[DownloadInfo, str]:
        page = "https://www.dev-c.com/gtav/scripthookv/"
        response = self._get(page, timeout=30)
        soup = BeautifulSoup(response.text, "html.parser")
        candidates: list[tuple[tuple[int, ...], str]] = []
        for link in soup.select("a[href]"):
            href = link.get("href") or ""
            if not href.lower().endswith(".zip"):
                continue
            name = Path(urlparse(href).path).name
            if "scripthookv" not in name.lower() or "sdk" in name.lower():
                continue
            version = tuple(int(part) for part in re.findall(r"\d+", name)[:3])
            candidates.append((version, _absolute(href, page)))
        if not candidates:
            raise SiteError(
                "Не удалось найти ссылку Script Hook V.",
                "Скачайте Script Hook V вручную с dev-c.com.",
            )
        candidates.sort(reverse=True)
        version_key, url = candidates[0]
        version = ".".join(str(part) for part in version_key[:2])
        filename = unquote(Path(urlparse(url).path).name)
        return DownloadInfo(url, filename, page, page), version

    def download_scripthookv(
        self,
        *,
        progress: ProgressCB = noop_progress,
        cancel=None,
        dest_dir: Path | None = None,
    ) -> tuple[DownloadedFile, str]:
        info, version = self.resolve_scripthookv()
        downloaded = self.download_file(
            info,
            dest_dir=dest_dir or (DOWNLOADS_DIR / "deps" / "shv"),
            progress=progress,
            cancel=cancel,
        )
        return downloaded, version

    def download_gta5mods_tool(
        self,
        key: str,
        page_url: str,
        *,
        progress: ProgressCB = noop_progress,
        cancel=None,
    ) -> DownloadedFile:
        details = self.fetch_mod(page_url)
        info = self.resolve_download(details)
        return self.download_file(
            info,
            dest_dir=DOWNLOADS_DIR / "deps" / key,
            progress=progress,
            cancel=cancel,
        )


GITHUB_DEPS: dict[str, dict] = {
    "shvdn": {
        "title": "Script Hook V .NET",
        "repo": "scripthookvdotnet/scripthookvdotnet",
        "preferred_tags": ("v3.6.0",),
        "page": "https://github.com/scripthookvdotnet/scripthookvdotnet/releases",
        "asset_patterns": ("ScriptHookVDotNet",),
    },
    "lemonui": {
        "title": "LemonUI",
        "repo": "LemonUIbyLemon/LemonUI",
        "preferred_tags": ("v2.2",),
        "page": "https://github.com/LemonUIbyLemon/LemonUI/releases",
        "asset_patterns": ("LemonUI",),
    },
    "nativeui": {
        "title": "NativeUI",
        "repo": "Guad/NativeUI",
        "preferred_tags": ("1.9.1",),
        "page": "https://github.com/Guad/NativeUI/releases",
        "asset_patterns": ("NativeUI", "Release"),
    },
}

GITHUB_PINNED: dict[str, tuple[str, str]] = {
    "scripthookvdotnet/scripthookvdotnet": (
        "https://github.com/scripthookvdotnet/scripthookvdotnet/releases/download/v3.6.0/ScriptHookVDotNet.zip",
        "ScriptHookVDotNet.zip",
    ),
    "LemonUIbyLemon/LemonUI": (
        "https://github.com/LemonUIbyLemon/LemonUI/releases/download/v2.2/LemonUI.zip",
        "LemonUI.zip",
    ),
    "Guad/NativeUI": (
        "https://github.com/Guad/NativeUI/releases/download/1.9.1/Release.zip",
        "Release.zip",
    ),
}


def _choose_github_asset(assets: list[dict], patterns: tuple[str, ...]) -> dict | None:
    best: tuple[int, dict] | None = None
    for asset in assets:
        name = str(asset.get("name") or "")
        low = name.lower()
        if not low.endswith(".zip") or "source" in low or "src" in low or "nightly" in low:
            continue
        score = 0
        if any(pattern.lower() in low for pattern in patterns):
            score += 30
        if "x64" in low:
            score += 5
        if low.startswith("release"):
            score += 2
        if best is None or score > best[0]:
            best = (score, asset)
    return best[1] if best else None


def _check_magic(path: Path, suffix: str) -> str:
    signatures = ARCHIVE_MAGIC.get(suffix)
    try:
        with open(path, "rb") as handle:
            head = handle.read(512)
    except OSError as exc:
        return str(exc)
    lowered = head.lstrip().lower()
    if lowered.startswith(b"<!doctype") or lowered.startswith(b"<html"):
        return "Похоже, вместо файла скачалась веб-страница."
    if signatures and not any(head.startswith(signature) for signature in signatures):
        return "Сигнатура архива не совпадает с расширением."
    return ""


def _unlink(path: Path) -> None:
    try:
        if path.exists():
            path.unlink()
    except OSError:
        pass


def download_dependency(
    key: str,
    *,
    progress: ProgressCB = noop_progress,
    cancel=None,
    client: SiteClient | None = None,
) -> tuple[Path, str]:
    own = client is None
    client = client or SiteClient()
    try:
        from .deps_defs import DEP_BY_KEY

        dep = DEP_BY_KEY.get(key)
        if dep is None:
            raise SiteError(f"Неизвестная зависимость: {key}")
        if not dep.auto:
            raise ManualDependencyError(
                f"«{dep.name}» устанавливается вручную.",
                dep.url,
            )
        if key == "shv":
            downloaded, version = client.download_scripthookv(progress=progress, cancel=cancel)
            return downloaded.path, version
        if key in GITHUB_DEPS:
            downloaded, version = client.download_github_dependency(key, progress=progress, cancel=cancel)
            return downloaded.path, version
        downloaded = client.download_gta5mods_tool(key, dep.url, progress=progress, cancel=cancel)
        return downloaded.path, ""
    finally:
        if own:
            client.close()
