"""Publishing: YouTube Shorts, Facebook Reels, Instagram Reels.

NOTE: these call live platform APIs and need real credentials; they have not been run against
your accounts. Test with privacy: private (YouTube) before turning on auto-publish.
"""
import pathlib
import time

import requests

from .util import env, log, read_json


# ------------------------------------------------------------------ YouTube
def youtube(video, meta, cfg):
    """Resumable upload with an OAuth refresh token (YT_CLIENT_ID / YT_CLIENT_SECRET / YT_REFRESH_TOKEN)."""
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": env("YT_CLIENT_ID"), "client_secret": env("YT_CLIENT_SECRET"),
        "refresh_token": env("YT_REFRESH_TOKEN"), "grant_type": "refresh_token"}, timeout=60)
    tok.raise_for_status()
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
def facebook_reel(video, meta, cfg):
    page, token = env("FB_PAGE_ID"), env("FB_PAGE_TOKEN")
    g = "https://graph.facebook.com/v21.0"
    r = requests.post(f"{g}/{page}/video_reels", data={"upload_phase": "start", "access_token": token}, timeout=60)
    r.raise_for_status()
    vid, upload_url = r.json()["video_id"], r.json()["upload_url"]
    data = pathlib.Path(video).read_bytes()
    u = requests.post(upload_url, data=data, timeout=1800, headers={
        "Authorization": "OAuth " + token, "offset": "0", "file_size": str(len(data))})
    u.raise_for_status()
    f = requests.post(f"{g}/{page}/video_reels", data={
        "upload_phase": "finish", "video_id": vid, "video_state": "PUBLISHED",
        "description": meta["description"][:2000], "access_token": token}, timeout=120)
    f.raise_for_status()
    log("Facebook reel published:", vid)
    return vid


# ------------------------------------------------------------------ Instagram Reels
def instagram_reel(public_video_url, meta, cfg):
    """Instagram needs a PUBLIC https URL of the mp4 (e.g. a GitHub Release asset on a public repo, or S3)."""
    ig, token = env("IG_USER_ID"), env("FB_PAGE_TOKEN")
    g = "https://graph.facebook.com/v21.0"
    c = requests.post(f"{g}/{ig}/media", data={
        "media_type": "REELS", "video_url": public_video_url,
        "caption": meta["description"][:2000], "access_token": token}, timeout=120)
    c.raise_for_status()
    cid = c.json()["id"]
    for _ in range(60):
        s = requests.get(f"{g}/{cid}", params={"fields": "status_code", "access_token": token}, timeout=60).json()
        if s.get("status_code") == "FINISHED":
            break
        if s.get("status_code") == "ERROR":
            raise RuntimeError("Instagram processing error: " + str(s))
        time.sleep(10)
    p = requests.post(f"{g}/{ig}/media_publish", data={"creation_id": cid, "access_token": token}, timeout=120)
    p.raise_for_status()
    log("Instagram reel published:", p.json().get("id"))
    return p.json().get("id")


def publish_all(out_dir, cfg, public_url=None):
    out_dir = pathlib.Path(out_dir)
    meta = read_json(out_dir / "meta.json")
    video = out_dir / "final.mp4"
    pub = cfg["publish"]
    if pub.get("youtube"):
        youtube(video, meta, cfg)
    if pub.get("facebook"):
        facebook_reel(video, meta, cfg)
    if pub.get("instagram"):
        if not public_url:
            log("Instagram skipped: pass --public-url (a public https link to final.mp4)")
        else:
            instagram_reel(public_url, meta, cfg)
