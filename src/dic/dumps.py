"""Download Wikimedia's current complete articles dump, verifying its SHA-1."""
import hashlib
import json
import os
import re
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

USER_AGENT = "dic/0.1 (Wiktionary etymology research; local dump importer)"


def fetch(url, destination):
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as out:
        while chunk := response.read(1024 * 1024):
            out.write(chunk)


def sha1(path):
    digest = hashlib.sha1()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def download(directory: Path, edition="ro", force=False):
    if edition not in {"ro", "en"}:
        raise ValueError("Only ro and en Wiktionary are supported")
    directory.mkdir(parents=True, exist_ok=True)
    wiki = f"{edition}wiktionary"
    base = f"https://dumps.wikimedia.org/{wiki}/latest/"
    filename = f"{wiki}-latest-pages-articles.xml.bz2"
    sums = directory / f"{wiki}-latest-sha1sums.txt"
    temporary = sums.with_suffix(".part")
    fetch(base + sums.name, temporary)
    os.replace(temporary, sums)
    checksum_rows = [line.split() for line in sums.read_text().splitlines() if len(line.split()) == 2]
    row = next((parts for parts in checksum_rows if re.fullmatch(
        rf"{wiki}-(?:latest|\d{{8}})-pages-articles\.xml\.bz2", parts[1])), None)
    expected = row[0] if row else None
    if not expected or not re.fullmatch(r"[a-f0-9]{40}", expected):
        raise ValueError(f"No valid checksum for {filename}")
    dated_filename = row[1]
    dump_date = dated_filename.split('-')[1]
    # Wikimedia's latest checksums contain dated filenames. Pin the download to
    # that date so a rotating latest symlink cannot change it mid-download.
    url = (f"https://dumps.wikimedia.org/{wiki}/{dump_date}/{dated_filename}"
           if dump_date != 'latest' else base + filename)
    target = directory / filename
    if force or not target.exists() or sha1(target) != expected:
        part = target.with_suffix(target.suffix + ".part")
        fetch(url, part)
        if sha1(part) != expected:
            raise ValueError("Dump checksum mismatch; rerun download (latest may have changed)")
        os.replace(part, target)
    # A latest URL moves: retain the checksum, retrieval time and compressed size.
    manifest = {"edition": edition, "url": url, "dump_date": dump_date, "sha1": expected,
                "bytes": target.stat().st_size,
                "verified_at": datetime.now(timezone.utc).isoformat()}
    target.with_suffix(target.suffix + ".json").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Verified {target} ({manifest['bytes']:,} bytes)")
    return target


def download_pinned(source: Path, directory: Path):
    """Fetch exactly the versioned snapshot, never a moving latest symlink."""
    manifest = json.loads(source.read_text())
    expected = manifest['sha1']
    filename = manifest['filename']
    if not re.fullmatch(r'(?:ro|en)wiktionary-\d{8}-pages-articles\.xml\.bz2', filename):
        raise ValueError('Invalid pinned dump filename')
    if not re.fullmatch(r'[a-f0-9]{40}',expected):
        raise ValueError('Invalid pinned SHA-1')
    directory.mkdir(parents=True,exist_ok=True)
    target = directory / filename
    if not target.exists() or sha1(target) != expected:
        temporary = target.with_suffix(target.suffix+'.part')
        fetch(manifest['url'],temporary)
        if sha1(temporary) != expected:
            raise ValueError('Pinned dump checksum mismatch')
        os.replace(temporary,target)
    target.with_suffix(target.suffix+'.json').write_text(json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    print(f'Verified pinned dump: {target}')
    return target
