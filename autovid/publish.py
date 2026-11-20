"""Publishing: YouTube Shorts, Facebook Reels, Instagram Reels.

These call live platform APIs and need real credentials. Start with
`publish.privacy: private` on YouTube, verify the Telegram approval flow, and only
then make the video public.
"""
import pathlib
import time

import requests

from .util import env, log, read_json

GRAPH = "https://graph.facebook.com/v21.0"


# ------------------------------------------------------------------ YouTube
def youtube(video, meta, cfg):
    """Resumable upload with an OAuth refresh token (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN)."""
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=60)
    if tok.status_code >= 400:
        # Google returns a useful OAuth error (for example invalid_grant) in
        # the response body. Include it so workflow reports identify the fix.
        raise RuntimeError(
            f"YouTube OAuth token exchange failed {tok.status_code}: {tok.text[:400]}"
        )
    access = tok.json()["access_token"]
    body = {
        "snippet": {"title": meta["title"][:100], "description": meta["description"][:4900],
                    "tags": meta.get("tags", [])[:15], "categoryId": "27", "defaultLanguage": "bn"},
        "status": {"privacyStatus": cfg["publish"].get("privacy", "private"),
                   "selfDeclaredMadeForKids": False,
                   "containsSyntheticMedia": bool(cfg["publish"].get("mark_synthetic", True))},
    }
    size = pathlib.Path(video).stat().st_size
    init = requests.post(
        "https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
        headers={"Authorization": "Bearer " + access, "Content-Type": "application/json; charset=UTF-8",
                 "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
        json=body, timeout=60)
    if init.status_code >= 400:
        raise RuntimeError(f"YouTube init failed {init.status_code}: {init.text[:400]}")
    url = init.headers["Location"]
    with open(video, "rb") as f:
        up = requests.put(url, data=f, headers={"Content-Type": "video/mp4"}, timeout=1800)
    if up.status_code >= 400:
        raise RuntimeError(f"YouTube upload failed {up.status_code}: {up.text[:400]}")
    vid = up.json().get("id")
    log("YouTube uploaded:", f"https://youtube.com/shorts/{vid}")
    return vid


# ------------------------------------------------------------------ Facebook Reels (Page)
def _fb_error(response, action):
    try:
        detail = response.json().get("error", {})
        message = detail.get("message") or response.text
        code = detail.get("code")
        return f"Facebook {action} failed {response.status_code} (code={code}): {str(message)[:500]}"
    except Exception:
        return f"Facebook {action} failed {response.status_code}: {response.text[:500]}"


def facebook_reel(video, meta, cfg):
    """Publish a Reel to a Facebook Page through the free Meta Graph API.

    Secrets: FB_PAGE_ID and FB_PAGE_TOKEN (a *Page* access token with
    pages_show_list, pages_read_engagement and pages_manage_posts).
    """
    page = env("FB_PAGE_ID")
    token = env("FB_PAGE_TOKEN")
    path = pathlib.Path(video)

    start = requests.post(f"{GRAPH}/{page}/video_reels", data={
        "upload_phase": "start", "access_token": token}, timeout=60)
    if start.status_code >= 400:
        raise RuntimeError(_fb_error(start, "start"))
    payload = start.json()
    video_id, upload_url = payload["video_id"], payload["upload_url"]

    size = path.stat().st_size
    with open(path, "rb") as handle:
        upload = requests.post(upload_url, data=handle, timeout=1800, headers={
            "Authorization": "OAuth " + token,
            "Content-Type": "application/octet-stream",
            "offset": "0",
            "file_size": str(size),
        })
    if upload.status_code >= 400:
        raise RuntimeError(_fb_error(upload, "upload"))

    description = (meta.get("description") or meta.get("title") or "")[:2000]
    finish = requests.post(f"{GRAPH}/{page}/video_reels", data={
        "upload_phase": "finish",
        "video_id": video_id,
        "video_state": "PUBLISHED",
        "description": description,
        "access_token": token,
    }, timeout=120)
    if finish.status_code >= 400:
        raise RuntimeError(_fb_error(finish, "publish"))

    log("Facebook reel published:", video_id)
    return video_id


# Kept as a friendly alias for older scripts/docs.
facebook = facebook_reel


def facebook_check():
    """Verify FB_PAGE_ID + FB_PAGE_TOKEN before enabling real uploads."""
    page, token = env("FB_PAGE_ID", False), env("FB_PAGE_TOKEN", False)
    print(f"Facebook page ID : {page or '(missing FB_PAGE_ID)'}")
    if not page or not token:
        print("Page token       : MISSING (set FB_PAGE_ID and FB_PAGE_TOKEN)")
        return False
    print("Page token       : present")
    try:
        r = requests.get(f"{GRAPH}/{page}", params={
            "fields": "id,name", "access_token": token}, timeout=60)
        if r.status_code >= 400:
            print(f"Page check       : FAILED {_fb_error(r, 'page check')}")
            return False
        data = r.json()
        print(f"Page check       : OK ({data.get('name', 'unknown')} / {data.get('id')})")
    except Exception as exc:
        print(f"Page check       : FAILED {exc}")
        return False
    try:
        perms = requests.get(f"{GRAPH}/{page}/permissions", params={
            "access_token": token}, timeout=60)
        if perms.status_code < 400:
            rows = perms.json().get("data", [])
            granted = [row.get("permission") for row in rows if row.get("status") == "granted"]
            print("Permissions      : " + (", ".join(granted) or "(none reported)"))
        else:
            print("Permissions      : could not read (this is normal for some Page tokens)")
    except Exception:
        pass
    print("Result           : READY (needs pages_manage_posts to publish)")
    return True


# ------------------------------------------------------------------ Instagram Reels
def instagram_reel(public_video_url, meta, cfg):
    """Instagram needs a PUBLIC https URL of the mp4 (GitHub Release asset, S3, etc.)."""
    ig, token = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    c = requests.post(f"{GRAPH}/{ig}/media", data={
        "media_type": "REELS", "video_url": public_video_url,
        "caption": meta["description"][:2000], "access_token": token}, timeout=120)
    c.raise_for_status()
    cid = c.json()["id"]
    for _ in range(60):
        s = requests.get(f"{GRAPH}/{cid}", params={"fields": "status_code", "access_token": token}, timeout=60).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError("Instagram processing error: " + str(s))
        time.sleep(10)
    p = requests.post(f"{GRAPH}/{ig}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=120)
    p.raise_for_status()
    log("Instagram reel published:", p.json().get("id"))
    return p.json().get("id")


def publish_all(out_dir, cfg, public_url=None):
    """Publish to every enabled platform, continuing if one platform fails.

    Returns {"results": {platform: id/url}, "errors": {platform: message}}.
    """
    out_dir = pathlib.Path(out_dir)
    meta = read_json(out_dir / "meta.json")
    video = out_dir / "final.mp4"
    pub = cfg["publish"]
    results, errors = {}, {}

    if pub.get("youtube"):
        try:
            vid = youtube(video, meta, cfg)
            results["youtube"] = f"https://youtube.com/shorts/{vid}"
        except (Exception, SystemExit) as exc:
            errors["youtube"] = str(exc)
    if pub.get("facebook"):
        try:
            results["facebook"] = facebook_reel(video, meta, cfg)
        except (Exception, SystemExit) as exc:
            errors["facebook"] = str(exc)
    if pub.get("instagram"):
        if not public_url:
            errors["instagram"] = "skipped: pass --public-url (a public https link to final.mp4)"
        else:
            try:
                results["instagram"] = instagram_reel(public_url, meta, cfg)
            except (Exception, SystemExit) as exc:
                errors["instagram"] = str(exc)

    for name, error in errors.items():
        log(f"{name}: {error}")
    return {"results": results, "errors": errors}
