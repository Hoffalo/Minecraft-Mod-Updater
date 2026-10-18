"""Minecraft mod updater.

Scans the mods folder, looks every jar up on Modrinth by its SHA-1 hash and
finds the newest build for the target Minecraft version and loader.

New builds are downloaded into a staging folder next to the mods folder,
named "mods <version>". If every mod has a build for the target version, the
staged jars replace the old ones right away (the old jars are moved to
"mods backup/<timestamp>"). If some mods are not out yet, the staging folder
is kept and you run `check` again later; once the last mod shows up, the
swap happens and you get a notification.

Settings (environment variables):
    MC_VERSION   Minecraft version to target, e.g. 26.4 (required, or --mc)
    MC_LOADER    fabric / quilt / forge / neoforge (default: detected from jars)
    MC_MODS_DIR  mods folder (default: %APPDATA%\\.minecraft\\mods)
    MC_ALLOW_PRERELEASE  set to 1 to take betas even when a stable release exists

Only the Python standard library is used.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from collections import Counter
from datetime import datetime
from pathlib import Path

API = "https://api.modrinth.com/v2"
USER_AGENT = "lolo/minecraft-mod-updater/1.0 (personal script)"
STATE_FILE = ".mod-updater.json"


# --------------------------------------------------------------------------
# Modrinth API
# --------------------------------------------------------------------------

def api(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        API + path,
        data=data,
        method=method,
        headers={"User-Agent": USER_AGENT, "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as e:
        sys.exit(f"Modrinth API error {e.code} on {path}: {e.read().decode(errors='replace')[:300]}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach Modrinth: {e.reason}")


def identify(hashes):
    """sha1 -> Modrinth version for the exact file (missing if unknown)."""
    if not hashes:
        return {}
    return api("POST", "/version_files", {"hashes": hashes, "algorithm": "sha1"})


def latest_for(hashes, loaders, mc_version):
    """sha1 -> newest version of that project for the given loader/version."""
    if not hashes:
        return {}
    return api("POST", "/version_files/update", {
        "hashes": hashes,
        "algorithm": "sha1",
        "loaders": loaders,
        "game_versions": [mc_version],
    })


def newest_release(project_id, loaders, mc_version):
    """Newest stable release of a project for loader/version, or None."""
    query = urllib.parse.urlencode({
        "loaders": json.dumps(loaders),
        "game_versions": json.dumps([mc_version]),
    })
    versions = api("GET", f"/project/{project_id}/version?{query}")
    return next((v for v in versions if v["version_type"] == "release"), None)


def project_titles(project_ids):
    if not project_ids:
        return {}
    ids = urllib.parse.quote(json.dumps(sorted(project_ids)))
    return {p["id"]: p["title"] for p in api("GET", f"/projects?ids={ids}")}


def primary_file(version):
    files = version["files"]
    return next((f for f in files if f.get("primary")), files[0])


def download(file_info, dest_dir):
    dest = dest_dir / file_info["filename"]
    req = urllib.request.Request(file_info["url"], headers={"User-Agent": USER_AGENT})
    tmp = dest.with_suffix(dest.suffix + ".part")
    with urllib.request.urlopen(req, timeout=120) as resp, open(tmp, "wb") as out:
        shutil.copyfileobj(resp, out)
    if sha1(tmp) != file_info["hashes"]["sha1"]:
        tmp.unlink()
        raise RuntimeError(f"hash mismatch downloading {file_info['filename']}")
    os.replace(tmp, dest)
    return dest


# --------------------------------------------------------------------------
# Local files
# --------------------------------------------------------------------------

def sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def jar_loader(path):
    try:
        with zipfile.ZipFile(path) as z:
            names = set(z.namelist())
    except zipfile.BadZipFile:
        return None
    if "quilt.mod.json" in names:
        return "quilt"
    if "fabric.mod.json" in names:
        return "fabric"
    if "META-INF/neoforge.mods.toml" in names:
        return "neoforge"
    if "META-INF/mods.toml" in names:
        return "forge"
    return None


def detect_loader(jars):
    votes = Counter(l for l in map(jar_loader, jars) if l)
    if not votes:
        sys.exit("Could not detect the mod loader from the jars; set MC_LOADER.")
    return votes.most_common(1)[0][0]


def loader_list(loader):
    # Quilt runs Fabric mods too, so accept Fabric builds as a fallback.
    return ["quilt", "fabric"] if loader == "quilt" else [loader]


def notify(title, message):
    print(f"\n*** {title}: {message} ***\a")
    if os.name != "nt":
        return
    title, message = (s.replace("'", "''") for s in (title, message))
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] > $null
$t = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
$txt = $t.GetElementsByTagName('text')
$txt.Item(0).AppendChild($t.CreateTextNode('{title}')) > $null
$txt.Item(1).AppendChild($t.CreateTextNode('{message}')) > $null
$app = '{{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}}\\WindowsPowerShell\\v1.0\\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($app).Show([Windows.UI.Notifications.ToastNotification]::new($t))
"""
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command", script],
                       capture_output=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        pass


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

class Config:
    def __init__(self, args):
        self.mc_version = args.mc or os.environ.get("MC_VERSION")
        if not self.mc_version:
            sys.exit("Set the MC_VERSION environment variable (or pass --mc), e.g. MC_VERSION=26.4")
        default_mods = Path(os.environ.get("APPDATA", Path.home())) / ".minecraft" / "mods"
        self.mods_dir = Path(args.mods_dir or os.environ.get("MC_MODS_DIR") or default_mods)
        if not self.mods_dir.is_dir():
            sys.exit(f"Mods folder not found: {self.mods_dir}")
        self.loader = (args.loader or os.environ.get("MC_LOADER") or "").lower() or None
        self.staging_dir = self.mods_dir.parent / f"{self.mods_dir.name} {self.mc_version}"
        self.backup_root = self.mods_dir.parent / f"{self.mods_dir.name} backup"
        self.dry_run = args.dry_run
        self.allow_prerelease = args.prerelease or os.environ.get("MC_ALLOW_PRERELEASE") == "1"


def load_state(cfg):
    path = cfg.staging_dir / STATE_FILE
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"mc_version": cfg.mc_version, "loader": None, "mods": {}, "unknown": []}


def save_state(cfg, state):
    cfg.staging_dir.mkdir(exist_ok=True)
    (cfg.staging_dir / STATE_FILE).write_text(json.dumps(state, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------

def check(cfg):
    state = load_state(cfg)
    mods = state["mods"]  # project_id -> entry
    jars = sorted(cfg.mods_dir.glob("*.jar"))
    if not jars:
        sys.exit(f"No .jar files in {cfg.mods_dir}")

    state["loader"] = cfg.loader or state["loader"] or detect_loader(jars)
    loaders = loader_list(state["loader"])
    print(f"Target: Minecraft {cfg.mc_version} / {state['loader']}")
    print(f"Mods folder: {cfg.mods_dir}")

    # Forget mods whose jar was removed from the mods folder since last run.
    present = {p.name for p in jars}
    for pid in [pid for pid, m in mods.items() if m["old_file"] not in present]:
        print(f"  {mods[pid]['name']} was removed from the mods folder; no longer tracking it")
        staged = cfg.staging_dir / (mods[pid].get("new_file") or "")
        if mods[pid].get("new_file") and staged.exists() and not cfg.dry_run:
            staged.unlink()
        del mods[pid]

    # Identify jars we are not tracking yet.
    known = {m["old_sha1"] for m in mods.values()}
    hashes = {sha1(p): p for p in jars}
    new_hashes = [h for h in hashes if h not in known]
    found = identify(new_hashes)
    titles = project_titles({v["project_id"] for v in found.values()})
    state["unknown"] = sorted(hashes[h].name for h in new_hashes if h not in found)
    for h, version in found.items():
        mods[version["project_id"]] = {
            "name": titles.get(version["project_id"], hashes[h].name),
            "old_file": hashes[h].name,
            "old_sha1": h,
            "old_version": version["version_number"],
            "status": "missing",
            "new_file": None,
            "new_sha1": None,
            "new_version": None,
        }

    # Ask Modrinth for the newest build of every tracked mod.
    latest = latest_for([m["old_sha1"] for m in mods.values()], loaders, cfg.mc_version)
    downloads = []
    for pid, m in mods.items():
        version = latest.get(m["old_sha1"])
        if version is None:
            m["status"] = "missing"
            continue
        # Prefer a stable release over a newer beta/alpha; only fall back to
        # a pre-release when no release exists for this version at all.
        if version["version_type"] != "release" and not cfg.allow_prerelease:
            version = newest_release(pid, loaders, cfg.mc_version) or version
        m["prerelease"] = version["version_type"] != "release"
        f = primary_file(version)
        if f["hashes"]["sha1"] == m["old_sha1"]:
            if m["new_file"] and (cfg.staging_dir / m["new_file"]).exists() and not cfg.dry_run:
                (cfg.staging_dir / m["new_file"]).unlink()
            m.update(status="current", new_file=None, new_sha1=None, new_version=m["old_version"])
        elif f["hashes"]["sha1"] != m["new_sha1"] or not (cfg.staging_dir / m["new_file"]).exists():
            downloads.append((m, version, f))
        m["deps"] = [d["project_id"] for d in version.get("dependencies", [])
                     if d.get("dependency_type") == "required" and d.get("project_id")]

    if not cfg.dry_run and downloads:
        cfg.staging_dir.mkdir(exist_ok=True)
    for m, version, f in downloads:
        if cfg.dry_run:
            m.update(status="ready", new_version=version["version_number"])
            continue
        if m["new_file"] and (cfg.staging_dir / m["new_file"]).exists():
            (cfg.staging_dir / m["new_file"]).unlink()
        print(f"  downloading {m['name']} {version['version_number']}")
        download(f, cfg.staging_dir)
        m.update(status="ready", new_file=f["filename"], new_sha1=f["hashes"]["sha1"],
                 new_version=version["version_number"])

    report(state)
    warn_missing_deps(mods)

    missing = [m for m in mods.values() if m["status"] == "missing"]
    if cfg.dry_run:
        print("\n(dry run: nothing was downloaded or moved)")
        return
    if missing:
        save_state(cfg, state)
        print(f"\n{len(missing)} mod(s) not available for {cfg.mc_version} yet. "
              f"Staged the rest in: {cfg.staging_dir}")
        print("Run `check` again later; the swap happens once everything is available.")
        return
    swap(cfg, state)


def swap(cfg, state):
    ready = [m for m in state["mods"].values() if m["status"] == "ready"]
    if ready:
        backup = cfg.backup_root / datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup.mkdir(parents=True)
        done = []
        try:
            for m in ready:
                old, new = cfg.mods_dir / m["old_file"], cfg.staging_dir / m["new_file"]
                os.replace(old, backup / m["old_file"])
                done.append(("old", m))
                os.replace(new, cfg.mods_dir / m["new_file"])
                done.append(("new", m))
        except PermissionError as e:
            # Undo so the mods folder is never left half-updated.
            for kind, m in reversed(done):
                if kind == "new":
                    os.replace(cfg.mods_dir / m["new_file"], cfg.staging_dir / m["new_file"])
                else:
                    os.replace(backup / m["old_file"], cfg.mods_dir / m["old_file"])
            save_state(cfg, state)
            sys.exit(f"Could not move {e.filename} (is Minecraft running?). Nothing was changed.")
        print(f"\nOld jars backed up to: {backup}")

    if cfg.staging_dir.exists():
        shutil.rmtree(cfg.staging_dir)
    if state["unknown"]:
        print("Left untouched (not on Modrinth, check these yourself): " + ", ".join(state["unknown"]))
    if ready:
        notify("Mods updated", f"All mods are ready for Minecraft {cfg.mc_version}; "
                               f"{len(ready)} jar(s) replaced.")
    else:
        print(f"\nEverything is already up to date for Minecraft {cfg.mc_version}.")


def report(state):
    rows = sorted(state["mods"].values(), key=lambda m: m["name"].lower())
    labels = {"ready": "UPDATE", "current": "ok", "missing": "MISSING"}
    width = max((len(m["name"]) for m in rows), default=10)
    print()
    for m in rows:
        detail = {
            "ready": f"{m['old_version']} -> {m['new_version']}",
            "current": m["old_version"],
            "missing": f"{m['old_version']} (no build for this version yet)",
        }[m["status"]]
        if m["status"] == "ready" and m.get("prerelease"):
            detail += "  (pre-release: no stable build exists)"
        print(f"  {labels[m['status']]:<8} {m['name']:<{width}}  {detail}")
    for name in state["unknown"]:
        print(f"  {'?':<8} {name:<{width}}  not found on Modrinth")


def warn_missing_deps(mods):
    absent = {}  # dependency project_id -> names of mods that need it
    for m in mods.values():
        if m["status"] == "missing":
            continue
        for dep in m.get("deps", []):
            if dep not in mods:
                absent.setdefault(dep, []).append(m["name"])
    if absent:
        titles = project_titles(set(absent))
        print("\nWarning: required by Modrinth metadata but not installed "
              "(may be bundled inside the mod, check if the game fails to start):")
        for dep, users in sorted(absent.items(), key=lambda kv: titles.get(kv[0], kv[0])):
            print(f"  {titles.get(dep, dep)}  <- needed by {', '.join(users)}")


def status(cfg):
    path = cfg.staging_dir / STATE_FILE
    if not path.exists():
        print(f"Nothing pending for Minecraft {cfg.mc_version}.")
        return
    state = load_state(cfg)
    print(f"Pending update to Minecraft {cfg.mc_version} ({state['loader']}), staged in {cfg.staging_dir}")
    report(state)


def main():
    parser = argparse.ArgumentParser(description="Check Minecraft mods for updates on Modrinth.")
    parser.add_argument("command", nargs="?", default="check", choices=["check", "status"],
                        help="check: look for updates and apply when complete (default); "
                             "status: show what is pending without going online")
    parser.add_argument("--mc", help="Minecraft version (overrides MC_VERSION)")
    parser.add_argument("--loader", help="mod loader (overrides MC_LOADER)")
    parser.add_argument("--mods-dir", help="mods folder (overrides MC_MODS_DIR)")
    parser.add_argument("--dry-run", action="store_true", help="report only; download and move nothing")
    parser.add_argument("--prerelease", action="store_true",
                        help="take betas/alphas even when a stable release exists (or MC_ALLOW_PRERELEASE=1)")
    args = parser.parse_args()
    cfg = Config(args)
    (status if args.command == "status" else check)(cfg)


if __name__ == "__main__":
    main()
