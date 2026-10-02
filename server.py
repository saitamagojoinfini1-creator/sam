#!/usr/bin/env python3
"""NIGHTMARES Music v3.0 — Recherche illimitée + API key pour bot WhatsApp."""
from __future__ import annotations
import json, os, re, secrets, shutil, ssl, subprocess, sys, threading, time, urllib.parse, urllib.request
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CTX = ssl.create_default_context()
PORT = int(os.environ.get("NM_PORT", "8765"))
UA = "NIGHTMARES-Music/3.0"
CACHE: dict = {}
LOCK = threading.Lock()
DL_DIR = ROOT / "_downloads"
DL_DIR.mkdir(exist_ok=True)
TOOLS = ROOT / "tools"
DATA_DIR = ROOT / "data"
DATA_DIR.mkdir(exist_ok=True)
API_KEYS_FILE = DATA_DIR / "api_keys.json"
SETTINGS_FILE = DATA_DIR / "settings.json"

# ─── Settings & API Keys ───────────────────────────────────────────

def _load_json(path: Path, default):
    try:
        if path.is_file():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default

def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")

def load_settings() -> dict:
    s = _load_json(SETTINGS_FILE, {})
    defaults = {
        "max_results": 200,
        "download_ttl_hours": 2,
        "require_api_key": True,
        "site_name": "NIGHTMARES Music",
        "public_url": "",
    }
    for k, v in defaults.items():
        s.setdefault(k, v)
    return s

def save_settings(s: dict):
    _save_json(SETTINGS_FILE, s)

def load_api_keys() -> list:
    return _load_json(API_KEYS_FILE, [])

def save_api_keys(keys: list):
    _save_json(API_KEYS_FILE, keys)

def create_api_key(name: str = "bot") -> dict:
    keys = load_api_keys()
    key = "nm_" + secrets.token_urlsafe(32)
    entry = {
        "key": key,
        "name": name or "bot",
        "created_at": int(time.time()),
        "last_used": None,
        "uses": 0,
        "enabled": True,
    }
    keys.append(entry)
    save_api_keys(keys)
    return entry

def revoke_api_key(key: str) -> bool:
    keys = load_api_keys()
    new = [k for k in keys if k.get("key") != key]
    if len(new) == len(keys):
        return False
    save_api_keys(new)
    return True

def check_api_key(provided: str | None) -> tuple[bool, str]:
    """Returns (ok, error_message)."""
    settings = load_settings()
    if not settings.get("require_api_key", True):
        return True, ""
    if not provided:
        return False, "Clé API manquante. Header: X-API-Key ou ?api_key="
    keys = load_api_keys()
    for k in keys:
        if k.get("key") == provided and k.get("enabled", True):
            k["last_used"] = int(time.time())
            k["uses"] = int(k.get("uses") or 0) + 1
            save_api_keys(keys)
            return True, ""
    return False, "Clé API invalide ou désactivée"

def extract_api_key(handler) -> str | None:
    h = handler.headers.get("X-API-Key") or handler.headers.get("Authorization") or ""
    if h.lower().startswith("bearer "):
        h = h[7:].strip()
    if h:
        return h.strip()
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(handler.path).query)
    return (qs.get("api_key") or [None])[0]

# ─── HTTP helpers ──────────────────────────────────────────────────

def _cleanup_downloads():
    settings = load_settings()
    max_age = float(settings.get("download_ttl_hours", 2)) * 3600
    now = time.time()
    for p in DL_DIR.glob("*"):
        try:
            if p.is_file() and now - p.stat().st_mtime > max_age:
                p.unlink(missing_ok=True)
        except Exception:
            pass

def http_json(url: str, timeout: int = 15):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode("utf-8", "replace"))

def _fmt(sec) -> str:
    try:
        s = int(float(sec))
        return f"{s // 60}:{s % 60:02d}"
    except Exception:
        return "—"

# ─── Search (illimité / pagination) ────────────────────────────────

def search_deezer_page(q: str, index: int = 0, limit: int = 50) -> list:
    url = (
        "https://api.deezer.com/search?q="
        + urllib.parse.quote(q)
        + f"&limit={limit}&index={index}"
    )
    data = http_json(url, timeout=12)
    out = []
    for t in data.get("data") or []:
        art = (t.get("artist") or {}).get("name") or ""
        alb = (t.get("album") or {}).get("title") or ""
        cover = (t.get("album") or {}).get("cover_medium") or (t.get("album") or {}).get("cover") or ""
        out.append({
            "title": t.get("title") or t.get("title_short") or "Sans titre",
            "artist": art,
            "duration": _fmt(t.get("duration")),
            "duration_sec": int(t.get("duration") or 0),
            "thumbnail": cover,
            "popularity": int(t.get("rank") or 0) // 1000,
            "album": alb,
            "preview": t.get("preview") or "",
            "source": "deezer",
            "id": f"dz-{t.get('id')}",
            "deezer_link": t.get("link") or "",
            "deezer_artist_id": (t.get("artist") or {}).get("id"),
        })
    total = int(data.get("total") or 0)
    return out, total

def search_deezer_artist_tracks(artist_id: int, max_tracks: int = 500) -> list:
    """Toutes les tracks d'un artiste Deezer (pagination)."""
    out, index = [], 0
    while len(out) < max_tracks:
        url = f"https://api.deezer.com/artist/{artist_id}/top?limit=50&index={index}"
        try:
            data = http_json(url, timeout=12)
        except Exception:
            break
        batch = data.get("data") or []
        if not batch:
            break
        for t in batch:
            art = (t.get("artist") or {}).get("name") or ""
            alb = (t.get("album") or {}).get("title") or ""
            cover = (t.get("album") or {}).get("cover_medium") or (t.get("album") or {}).get("cover") or ""
            out.append({
                "title": t.get("title") or t.get("title_short") or "Sans titre",
                "artist": art,
                "duration": _fmt(t.get("duration")),
                "duration_sec": int(t.get("duration") or 0),
                "thumbnail": cover,
                "popularity": int(t.get("rank") or 0) // 1000,
                "album": alb,
                "preview": t.get("preview") or "",
                "source": "deezer-artist",
                "id": f"dz-{t.get('id')}",
                "deezer_link": t.get("link") or "",
            })
        index += len(batch)
        if len(batch) < 50:
            break
        if index >= 2000:
            break
    return out

def search_itunes(q: str, limit: int = 50) -> list:
    data = http_json(
        "https://itunes.apple.com/search?term="
        + urllib.parse.quote(q)
        + f"&media=music&entity=song&limit={min(limit, 200)}",
        timeout=12,
    )
    out = []
    for t in data.get("results") or []:
        out.append({
            "title": t.get("trackName") or "Sans titre",
            "artist": t.get("artistName") or "",
            "duration": _fmt((t.get("trackTimeMillis") or 0) / 1000),
            "duration_sec": int((t.get("trackTimeMillis") or 0) / 1000),
            "thumbnail": (t.get("artworkUrl100") or "").replace("100x100", "300x300"),
            "popularity": 0,
            "album": t.get("collectionName") or "",
            "preview": t.get("previewUrl") or "",
            "source": "itunes",
            "id": f"it-{t.get('trackId')}",
        })
    return out

def search_all(q: str, limit: int = 200, artist_mode: bool = False) -> dict:
    """Recherche large. Si artist_mode, charge la discographie de l'artiste trouvé."""
    settings = load_settings()
    limit = min(int(limit or settings.get("max_results", 200)), 1000)
    results, seen = [], set()
    total_hint = 0

    # 1) Pages Deezer search
    try:
        page, total_hint = search_deezer_page(q, 0, min(50, limit))
        for t in page:
            key = (t["title"].lower().strip(), t["artist"].lower().strip())
            if key not in seen:
                seen.add(key)
                results.append(t)
        # pages suivantes
        idx = 50
        while len(results) < limit and idx < total_hint and idx < 500:
            more, _ = search_deezer_page(q, idx, 50)
            if not more:
                break
            for t in more:
                key = (t["title"].lower().strip(), t["artist"].lower().strip())
                if key not in seen:
                    seen.add(key)
                    results.append(t)
            idx += 50
            if len(more) < 50:
                break
    except Exception as e:
        print("[search deezer]", e)

    # 2) Mode artiste : discographie complète
    if artist_mode or (results and len(q.split()) <= 3):
        artist_ids = []
        for t in results[:5]:
            aid = t.get("deezer_artist_id")
            if aid and aid not in artist_ids:
                # vérifie que le nom artiste matche la requête
                if q.lower() in (t.get("artist") or "").lower() or (t.get("artist") or "").lower() in q.lower():
                    artist_ids.append(aid)
        for aid in artist_ids[:2]:
            try:
                tracks = search_deezer_artist_tracks(aid, max_tracks=limit)
                for t in tracks:
                    key = (t["title"].lower().strip(), t["artist"].lower().strip())
                    if key not in seen:
                        seen.add(key)
                        results.append(t)
            except Exception as e:
                print("[artist tracks]", e)

    # 3) iTunes complément
    try:
        for t in search_itunes(q, limit=min(50, limit)):
            key = (t["title"].lower().strip(), t["artist"].lower().strip())
            if key not in seen:
                seen.add(key)
                results.append(t)
    except Exception as e:
        print("[search itunes]", e)

    results.sort(key=lambda x: x.get("popularity") or 0, reverse=True)
    results = results[:limit]
    return {
        "result": results,
        "count": len(results),
        "total_hint": total_hint,
        "query": q,
        "limit": limit,
    }

# ─── Lyrics ────────────────────────────────────────────────────────

def fetch_lyrics(artist: str, title: str) -> dict:
    key = f"ly:{artist.lower()}|{title.lower()}"
    now = time.time()
    with LOCK:
        hit = CACHE.get(key)
        if hit and now - hit["ts"] < 3600:
            return hit["data"]

    clean_title = re.sub(r"\(.*?\)|\[.*?\]|ft\..*|feat\..*", "", title, flags=re.I).strip()
    clean_artist = re.sub(r"\(.*?\)|\[.*?\]", "", artist).strip()

    try:
        url = "https://lrclib.net/api/get?" + urllib.parse.urlencode({
            "artist_name": clean_artist or artist, "track_name": clean_title or title
        })
        data = http_json(url, timeout=12)
        plain = (data.get("plainLyrics") or "").strip()
        synced = (data.get("syncedLyrics") or "").strip()
        if plain or synced:
            out = {
                "status": True,
                "lyrics": plain or re.sub(r"\[\d+:\d+[.\d]*\]\s*", "", synced),
                "synced": synced or None,
                "source": "lrclib",
            }
            with LOCK:
                CACHE[key] = {"ts": now, "data": out}
            return out
    except Exception as e:
        print("[lyrics lrclib]", e)

    try:
        url = "https://lrclib.net/api/search?" + urllib.parse.urlencode({
            "q": f"{clean_artist} {clean_title}"
        })
        arr = http_json(url, timeout=12)
        if isinstance(arr, list) and arr:
            best = arr[0]
            plain = (best.get("plainLyrics") or "").strip()
            if plain:
                out = {"status": True, "lyrics": plain, "synced": best.get("syncedLyrics"), "source": "lrclib-search"}
                with LOCK:
                    CACHE[key] = {"ts": now, "data": out}
                return out
    except Exception as e:
        print("[lyrics lrclib-search]", e)

    try:
        url = (
            "https://api.lyrics.ovh/v1/"
            + urllib.parse.quote(clean_artist or "x")
            + "/"
            + urllib.parse.quote(clean_title or title)
        )
        data = http_json(url, timeout=12)
        plain = (data.get("lyrics") or "").strip()
        if plain:
            out = {"status": True, "lyrics": plain, "synced": None, "source": "lyrics.ovh"}
            with LOCK:
                CACHE[key] = {"ts": now, "data": out}
            return out
    except Exception as e:
        print("[lyrics ovh]", e)

    return {"status": False, "error": "Paroles introuvables", "lyrics": "", "synced": None}

# ─── yt-dlp ────────────────────────────────────────────────────────

def _safe_name(s: str) -> str:
    s = re.sub(r'[\\/:*?"<>|\n\r]+', "_", str(s or ""))
    return s.strip(" ._")[:120] or "track"

def find_ytdlp() -> str | None:
    for p in [
        TOOLS / "yt-dlp.exe",
        TOOLS / "bin" / "yt-dlp.exe",
        ROOT / "yt-dlp.exe",
        shutil.which("yt-dlp"),
        shutil.which("yt-dlp.exe"),
    ]:
        if p and Path(p).is_file():
            return str(p)
    try:
        import yt_dlp  # noqa: F401
        return "python-module"
    except Exception:
        return None

def find_ffmpeg() -> str | None:
    for p in [
        shutil.which("ffmpeg"),
        r"C:\ffmpeg\bin\ffmpeg.exe",
    ]:
        if p and Path(p).is_file():
            return str(p)
    try:
        winget = Path.home() / "AppData/Local/Microsoft/WinGet/Packages"
        if winget.is_dir():
            for p in winget.glob("Gyan.FFmpeg*/**/ffmpeg.exe"):
                return str(p)
    except Exception:
        pass
    return None

def _find_downloaded(folder: Path, prefix: str) -> Path | None:
    cands = sorted(folder.glob(prefix + "*"), key=lambda p: p.stat().st_mtime, reverse=True)
    for p in cands:
        if p.is_file() and p.stat().st_size > 50000:
            return p
    return None

def download_with_ytdlp(query: str, title: str, artist: str) -> tuple[Path | None, str]:
    _cleanup_downloads()
    key = "yt:" + query.lower()
    now = time.time()
    with LOCK:
        hit = CACHE.get(key)
        if hit and now - hit["ts"] < 3600:
            p = Path(hit["path"])
            if p.is_file() and p.stat().st_size > 100000:
                return p, ""

    fname = _safe_name(f"{artist} - {title}")
    final = DL_DIR / (fname + ".mp3")
    if final.is_file() and final.stat().st_size > 100000:
        with LOCK:
            CACHE[key] = {"ts": now, "path": str(final)}
        return final, ""

    ytdlp = find_ytdlp()
    if not ytdlp:
        return None, "yt-dlp introuvable. pip install -U yt-dlp  OU place yt-dlp.exe dans tools/"

    ffmpeg = find_ffmpeg()
    searches = []
    if artist and title:
        searches.append(f"{artist} - {title} official audio")
        searches.append(f"{artist} {title} audio")
        searches.append(f"{artist} {title}")
    searches.append(query)
    searches = list(dict.fromkeys(s for s in searches if s.strip()))

    client_attempts = [
        "youtube:player_client=default,mweb",
        "youtube:player_client=mweb,android",
        "youtube:player_client=android,ios,web",
    ]

    last_err = ""
    prefix = f"media-{int(time.time()*1000)}-"
    outtmpl = str(DL_DIR / (prefix + ".%(ext)s"))

    for search_q in searches:
        input_arg = search_q if re.match(r"^https?://", search_q, re.I) else f"ytsearch1:{search_q}"
        for client_arg in client_attempts:
            try:
                print(f"[yt-dlp] {search_q[:60]} | {client_arg}")
                if ytdlp == "python-module":
                    import yt_dlp
                    opts = {
                        "format": "bestaudio/best",
                        "outtmpl": outtmpl,
                        "noplaylist": True,
                        "quiet": True,
                        "no_warnings": True,
                        "retries": 2,
                        "fragment_retries": 2,
                        "socket_timeout": 30,
                        "extractor_args": {"youtube": {"player_client": client_arg.split("=")[-1].split(",")}},
                    }
                    if ffmpeg:
                        opts["ffmpeg_location"] = str(Path(ffmpeg).parent)
                    with yt_dlp.YoutubeDL(opts) as ydl:
                        ydl.download([input_arg])
                else:
                    cmd = [
                        ytdlp, "-f", "bestaudio/best",
                        "--no-playlist", "-o", outtmpl,
                        "--retries", "2", "--socket-timeout", "30",
                        "--extractor-args", client_arg,
                        input_arg,
                    ]
                    if ffmpeg:
                        cmd.extend(["--ffmpeg-location", str(Path(ffmpeg).parent)])
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                    if r.returncode != 0:
                        last_err = (r.stderr or r.stdout or "")[-300:]
                        print("[yt-dlp stderr]", last_err[:200])
                        continue

                found = _find_downloaded(DL_DIR, prefix)
                if found:
                    if found.suffix.lower() != ".mp3" and ffmpeg:
                        try:
                            subprocess.run(
                                [ffmpeg, "-y", "-i", str(found), "-codec:a", "libmp3lame", "-q:a", "2", str(final)],
                                capture_output=True, timeout=90,
                            )
                            if final.is_file() and final.stat().st_size > 50000:
                                try:
                                    found.unlink(missing_ok=True)
                                except Exception:
                                    pass
                                found = final
                        except Exception as e:
                            print("[ffmpeg]", e)
                    elif found.suffix.lower() != ".mp3":
                        # rename if already ok size
                        try:
                            found.rename(final)
                            found = final
                        except Exception:
                            pass
                    with LOCK:
                        CACHE[key] = {"ts": time.time(), "path": str(found)}
                    print(f"[yt-dlp] OK {found.name} ({found.stat().st_size} bytes)")
                    return found, ""
            except subprocess.TimeoutExpired:
                last_err = "timeout yt-dlp"
                print("[yt-dlp] timeout")
            except Exception as e:
                last_err = str(e)
                print(f"[yt-dlp] fail: {last_err[:200]}")

    return None, last_err or "Echec yt-dlp — mets a jour: yt-dlp -U"

def _serve_file(handler, path_mp3: Path, as_attachment: bool = False, title: str = "", artist: str = ""):
    data = path_mp3.read_bytes()
    name = _safe_name(f"{artist} - {title}") + path_mp3.suffix
    ctype = "audio/mpeg" if path_mp3.suffix.lower() == ".mp3" else "application/octet-stream"
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Access-Control-Allow-Origin", "*")
    if as_attachment:
        handler.send_header("Content-Disposition", f'attachment; filename="{name}"')
    else:
        handler.send_header("Content-Disposition", f'inline; filename="{name}"')
    handler.end_headers()
    handler.wfile.write(data)

# ─── HTTP Handler ──────────────────────────────────────────────────

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        print(f"[http] {args[0]}")

    def _json(self, code: int, obj: dict):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-API-Key, Authorization")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            body = {}

        # API key management (local UI only — no key required for creating first key)
        if path == "/api/keys/create":
            name = str(body.get("name") or "bot").strip()[:64]
            entry = create_api_key(name)
            return self._json(200, {"status": True, "key": entry["key"], "name": entry["name"], "created_at": entry["created_at"]})

        if path == "/api/keys/revoke":
            key = str(body.get("key") or "").strip()
            ok = revoke_api_key(key)
            return self._json(200 if ok else 404, {"status": ok})

        if path == "/api/settings":
            s = load_settings()
            for k in ("max_results", "download_ttl_hours", "require_api_key", "site_name", "public_url"):
                if k in body:
                    s[k] = body[k]
            save_settings(s)
            return self._json(200, {"status": True, "settings": s})

        return self._json(404, {"status": False, "error": "route inconnue"})

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        # --- Public / UI routes (no API key) ---
        if path == "/api/ping":
            ytdlp = find_ytdlp()
            return self._json(200, {
                "status": True,
                "version": "3.0",
                "ytdlp": bool(ytdlp),
                "ytdlp_path": ytdlp or "",
                "ffmpeg": bool(find_ffmpeg()),
                "mode": "FULL_ONLY",
                "max_results": load_settings().get("max_results", 200),
            })

        if path == "/api/settings":
            s = load_settings()
            keys = load_api_keys()
            # mask keys for UI
            safe_keys = [{
                "key": k["key"][:8] + "…" + k["key"][-4:] if len(k.get("key", "")) > 12 else k.get("key"),
                "full_key": k["key"],  # UI needs full to copy once; user is local
                "name": k.get("name"),
                "created_at": k.get("created_at"),
                "last_used": k.get("last_used"),
                "uses": k.get("uses", 0),
                "enabled": k.get("enabled", True),
            } for k in keys]
            return self._json(200, {
                "status": True,
                "settings": s,
                "keys": safe_keys,
                "ytdlp": bool(find_ytdlp()),
                "ffmpeg": bool(find_ffmpeg()),
                "version": "3.0",
            })

        # UI search (no key required — browser local)
        if path == "/api/search":
            q = (qs.get("q") or [""])[0].strip()
            if not q:
                return self._json(400, {"status": False, "error": "q requis", "result": []})
            limit = int((qs.get("limit") or [0])[0] or load_settings().get("max_results", 200))
            artist_mode = (qs.get("artist") or ["0"])[0] in ("1", "true", "yes")
            try:
                data = search_all(q, limit=limit, artist_mode=artist_mode)
                data["status"] = True
                return self._json(200, data)
            except Exception as e:
                return self._json(502, {"status": False, "error": str(e), "result": []})

        if path == "/api/lyrics":
            artist = (qs.get("artist") or [""])[0].strip()
            title = (qs.get("title") or [""])[0].strip()
            if not title:
                return self._json(400, {"status": False, "error": "title requis"})
            try:
                data = fetch_lyrics(artist or "Unknown", title)
                return self._json(200 if data.get("status") else 404, data)
            except Exception as e:
                return self._json(502, {"status": False, "error": str(e), "lyrics": ""})

        # Stream / file for browser UI (no key)
        if path in ("/api/stream", "/api/file"):
            q = (qs.get("q") or [""])[0].strip()
            title = (qs.get("title") or ["track"])[0]
            artist = (qs.get("artist") or ["unknown"])[0]
            if not q:
                q = f"{artist} {title}".strip()
            if not q or q == "unknown track":
                return self._json(400, {"status": False, "error": "titre manquant"})
            try:
                path_mp3, err = download_with_ytdlp(q, title, artist)
                if not path_mp3 or not path_mp3.is_file():
                    return self._json(404, {
                        "status": False,
                        "error": err or "Morceau complet impossible. Mets a jour yt-dlp.",
                    })
                return _serve_file(self, path_mp3, as_attachment=(path == "/api/file"), title=title, artist=artist)
            except Exception as e:
                return self._json(502, {"status": False, "error": str(e)})

        # ─── BOT API (requires API key) ─────────────────────────────
        if path.startswith("/api/v1/"):
            api_key = extract_api_key(self)
            ok, err = check_api_key(api_key)
            if not ok:
                return self._json(401, {"status": False, "error": err})

            if path == "/api/v1/search":
                q = (qs.get("q") or [""])[0].strip()
                if not q:
                    return self._json(400, {"status": False, "error": "param q requis"})
                limit = int((qs.get("limit") or [50])[0] or 50)
                artist_mode = (qs.get("artist") or ["0"])[0] in ("1", "true", "yes")
                try:
                    data = search_all(q, limit=limit, artist_mode=artist_mode)
                    data["status"] = True
                    return self._json(200, data)
                except Exception as e:
                    return self._json(502, {"status": False, "error": str(e)})

            if path == "/api/v1/download":
                # Returns JSON with direct URL or streams file if format=file
                q = (qs.get("q") or [""])[0].strip()
                title = (qs.get("title") or ["track"])[0]
                artist = (qs.get("artist") or ["unknown"])[0]
                fmt = (qs.get("format") or ["json"])[0]
                if not q:
                    q = f"{artist} {title}".strip()
                if not q or q == "unknown track":
                    return self._json(400, {"status": False, "error": "title/q manquant"})
                try:
                    path_mp3, err = download_with_ytdlp(q, title, artist)
                    if not path_mp3 or not path_mp3.is_file():
                        return self._json(404, {"status": False, "error": err or "download failed"})
                    if fmt == "file":
                        return _serve_file(self, path_mp3, as_attachment=True, title=title, artist=artist)
                    # json mode: give a temporary stream path (same host)
                    # For bot: return file bytes info + suggest using format=file
                    return self._json(200, {
                        "status": True,
                        "title": title,
                        "artist": artist,
                        "size": path_mp3.stat().st_size,
                        "filename": path_mp3.name,
                        "download_url": f"/api/v1/download?q={urllib.parse.quote(q)}&title={urllib.parse.quote(title)}&artist={urllib.parse.quote(artist)}&format=file&api_key={api_key}",
                        "message": "Utilise download_url ou format=file pour récupérer le MP3",
                    })
                except Exception as e:
                    return self._json(502, {"status": False, "error": str(e)})

            if path == "/api/v1/lyrics":
                artist = (qs.get("artist") or [""])[0].strip()
                title = (qs.get("title") or [""])[0].strip()
                if not title:
                    return self._json(400, {"status": False, "error": "title requis"})
                data = fetch_lyrics(artist or "Unknown", title)
                return self._json(200 if data.get("status") else 404, data)

            if path == "/api/v1/ping":
                return self._json(200, {
                    "status": True,
                    "version": "3.0",
                    "api": "v1",
                    "ytdlp": bool(find_ytdlp()),
                    "ffmpeg": bool(find_ffmpeg()),
                })

            return self._json(404, {"status": False, "error": "endpoint v1 inconnu"})

        if path == "/":
            self.path = "/index.html"
        return super().do_GET()


def main():
    y = find_ytdlp()
    f = find_ffmpeg()
    print("=" * 52)
    print("  NIGHTMARES Music v3.0")
    print(f"  yt-dlp  : {y or 'MANQUANT'}")
    print(f"  ffmpeg  : {f or 'MANQUANT (conseille)'}")
    print(f"  => http://127.0.0.1:{PORT}/")
    print("  API bot : /api/v1/*  (header X-API-Key)")
    print("  NE FERME PAS cette fenetre")
    print("=" * 52)
    # Listen on all interfaces so tunnel / LAN works
    httpd = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.server_close()


if __name__ == "__main__":
    main()
