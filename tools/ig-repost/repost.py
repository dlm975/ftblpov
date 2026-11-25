#!/usr/bin/env python3
"""Repost new posts from a source Instagram account (used with its owner's
permission) as AI colourway concepts on your Instagram Business/Creator account.

Uses only the official Instagram Graph API:
  * Business Discovery to read the source account's public posts
  * Content Publishing to post them to your account

Generates unofficial football boot packs from eligible source photos. Run `python3 repost.py --help` for options.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPH = "https://graph.facebook.com/" + os.environ.get("GRAPH_VERSION", "v21.0")
CAPTION_MAX = 2200
HASHTAG_MAX = 30
HERE = Path(__file__).resolve().parent
SHORTCODE_RE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:p|reel|reels|tv)/([\w-]+)")


def shortcode_from(link):
    m = SHORTCODE_RE.search(link)
    if m:
        return m.group(1)
    if re.fullmatch(r"[\w-]{5,}", link):  # bare shortcode
        return link
    raise ValueError(f"not an Instagram post link: {link}")


class ApiError(RuntimeError):
    pass


def api(method, path, params=None):
    """Call the Graph API. Adds ACCESS_TOKEN if set (otherwise relies on a proxy that injects it)."""
    params = dict(params or {})
    token = os.environ.get("ACCESS_TOKEN")
    if token:
        params["access_token"] = token
    url = f"{GRAPH}/{path.lstrip('/')}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        body = e.read().decode(errors="replace")
        try:
            msg = json.loads(body)["error"]["message"]
        except (ValueError, KeyError):
            msg = body
        raise ApiError(f"{method} {path}: HTTP {e.code}: {msg}") from None


# ---------- reading the source account ----------

MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,media_url,permalink,timestamp,"
    "children{media_type,media_url}"
)


def fetch_source_posts(ig_user_id, source, limit):
    fields = f"business_discovery.username({source}){{username,media.limit({limit}){{{MEDIA_FIELDS}}}}}"
    data = api("GET", ig_user_id, {"fields": fields})
    posts = data["business_discovery"].get("media", {}).get("data", [])
    return sorted(posts, key=lambda p: p["timestamp"])  # oldest first


# ---------- publishing ----------

def wait_ready(container_id, timeout=600):
    """Videos are processed asynchronously; poll until the container is ready."""
    deadline = time.time() + timeout
    while True:
        status = api("GET", container_id, {"fields": "status_code,status"})
        code = status.get("status_code")
        if code in ("FINISHED", "PUBLISHED"):
            return
        if code in ("ERROR", "EXPIRED"):
            raise ApiError(f"container {container_id} {code}: {status.get('status')}")
        if time.time() > deadline:
            raise ApiError(f"container {container_id} still {code} after {timeout}s")
        time.sleep(10)


def create_container(ig_user_id, post, caption):
    mtype = post.get("media_type")
    if mtype == "IMAGE":
        return api("POST", f"{ig_user_id}/media", {"image_url": post["media_url"], "caption": caption})["id"]
    if mtype == "VIDEO":
        cid = api("POST", f"{ig_user_id}/media",
                  {"media_type": "REELS", "video_url": post["media_url"], "caption": caption})["id"]
        wait_ready(cid)
        return cid
    if mtype == "CAROUSEL_ALBUM":
        children = []
        for child in post["children"]["data"][:10]:
            if child.get("media_type") == "VIDEO":
                cid = api("POST", f"{ig_user_id}/media", {
                    "media_type": "VIDEO", "video_url": child["media_url"], "is_carousel_item": "true"})["id"]
                wait_ready(cid)
            else:
                cid = api("POST", f"{ig_user_id}/media",
                          {"image_url": child["media_url"], "is_carousel_item": "true"})["id"]
            wait_ready(cid)
            children.append(cid)
        cid = api("POST", f"{ig_user_id}/media",
                  {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption})["id"]
        wait_ready(cid)
        return cid
    raise ApiError(f"unsupported media_type {mtype}")


# ---------- state ----------

def load_state(path):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {"reposted": {}, "skipped": {}}


def save_state(path, state):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(path)
    if os.environ.get("PUSH_PACK_STATE") == "1":
        def git(*args):
            return subprocess.run(["git", "-C", str(path.parent), *args], check=True)
        git("add", "--", path.name)
        if subprocess.run(["git", "-C", str(path.parent), "diff", "--cached", "--quiet"]).returncode:
            git("commit", "-qm", "Checkpoint Instagram pack state")
        git("push", "origin", "HEAD:refs/heads/ig-repost-state")


# ---------- main ----------

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--ig-user-id", default=os.environ.get("IG_USER_ID"),
                   help="your Instagram Business/Creator account ID (or env IG_USER_ID)")
    p.add_argument("--source", default=os.environ.get("SOURCE_USERNAME", "cleats"),
                   help="username to repost from, without @ (default: cleats)")
    p.add_argument("--expect-account", default=os.environ.get("EXPECT_ACCOUNT", "ftblpov"),
                   help="refuse to post unless --ig-user-id is this username (default: ftblpov)")
    p.add_argument("links", nargs="*",
                   help="specific post/reel links to repost; without links, reposts the newest unposted posts")
    p.add_argument("--max-posts", type=int, default=1, help="most posts to publish this run (default 1)")
    p.add_argument("--lookback", type=int, default=None,
                   help="how many recent source posts to check (default 25, or 100 when links are given)")
    p.add_argument("--state", type=Path, default=HERE / "state.json", help="file tracking reposted posts")
    p.add_argument("--seed", action="store_true",
                   help="mark every current source post as already handled, post nothing "
                        "(run once first so only NEW posts get reposted)")
    p.add_argument("--dry-run", action="store_true", help="show what would be posted, post nothing")
    args = p.parse_args(argv)

    if not args.ig_user_id:
        p.error("set --ig-user-id or IG_USER_ID")
    source = args.source.lstrip("@")
    try:
        wanted = [shortcode_from(x) for x in args.links]
    except ValueError as e:
        p.error(str(e))
    lookback = args.lookback or (100 if wanted else 25)

    me = api("GET", args.ig_user_id, {"fields": "username"}).get("username")
    if me != "ftblpov" or me != args.expect_account.lstrip("@"):
        p.error(f"account {args.ig_user_id} is @{me}, not @{args.expect_account.lstrip('@')}; refusing to post")
    print(f"posting to @{me}")

    state = load_state(args.state)
    done = set(state["reposted"]) | set(state["skipped"])
    posts = fetch_source_posts(args.ig_user_id, source, lookback)
    if wanted:
        by_code = {shortcode_from(x.get("permalink", "")): x for x in posts if x.get("permalink")}
        new = []
        for code in dict.fromkeys(wanted):
            post = by_code.get(code)
            if not post:
                print(f"not found: {code} is not among @{source}'s last {lookback} posts "
                      f"(wrong account, or older: raise --lookback)", file=sys.stderr)
            elif post["id"] in state["reposted"]:
                print(f"already reposted: {post['permalink']}")
            else:
                new.append(post)
        args.max_posts = len(new)
    else:
        new = [x for x in posts if x["id"] not in done]
        print(f"@{source}: {len(posts)} recent posts, {len(new)} not yet handled")

    if args.seed:
        for x in new:
            state["skipped"][x["id"]] = {"permalink": x.get("permalink"), "reason": "seed"}
        save_state(args.state, state)
        print(f"Seeded {len(new)} posts; future runs repost only posts newer than these.")
        return 0

    published = 0
    for post in new:
        if published >= args.max_posts:
            break
        link = post.get("permalink")
        from colourway import generate, source_urls
        pending = state.setdefault("pending", {})
        cid = pending.get(post["id"])
        urls = source_urls(post)
        if args.dry_run:
            print(f"[dry run] {link}: {len(urls)} candidate photo(s), pending container {cid}")
            published += bool(urls or cid)
            continue
        if not cid:
            result = generate(post) if urls else None
            if result is None:
                state["skipped"][post["id"]] = {"permalink": link, "reason": "no eligible boot photos"}
                save_state(args.state, state)
                continue
            media, caption = result
            cid = create_container(args.ig_user_id, media, caption)
            pending[post["id"]] = cid
            save_state(args.state, state)
        status = api("GET", cid, {"fields": "status_code"})["status_code"]
        if status in ("ERROR", "EXPIRED"):
            del pending[post["id"]]
            save_state(args.state, state)
            raise ApiError(f"Container {cid} was {status}. A fresh render is needed.")
        record = {"permalink": link, "container_id": cid}
        if status != "PUBLISHED":
            wait_ready(cid)
            record["new_media_id"] = api("POST", f"{args.ig_user_id}/media_publish", {"creation_id": cid})["id"]
        del pending[post["id"]]
        state["reposted"][post["id"]] = record
        save_state(args.state, state)
        published += 1
        print(f"Published concept for {link} (container {cid})")

    if not published:
        print("nothing new to repost")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except ApiError as e:
        print(f"error: {e}", file=sys.stderr)
        sys.exit(1)
