#!/usr/bin/env python3
"""
purge_and_isolate.py — Purge WAM from all non-zhou Windows users.

Usage:
    python purge_and_isolate.py              # dry run (preview)
    python purge_and_isolate.py --execute    # actually clean
    python purge_and_isolate.py --execute --trim-log  # also trim zhou's wam.log
"""
import os, sys, json, shutil, sqlite3, argparse, glob
from pathlib import Path
from datetime import datetime

if sys.platform == 'win32':
    try: sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    except: pass

OWNER = 'zhou'
SKIP_USERS = {'Public', 'Default', 'Default User', 'All Users', 'DefaultAppPool'}
WAM_DIR_PATTERNS = ['local.wam*', 'local.wam-savior*', 'local.aiswitch*']
WAM_DB_PATTERNS = ['%wam%', '%savior%', '%aiswitch%']

def find_users():
    users_dir = Path('C:/Users')
    return [d.name for d in users_dir.iterdir()
            if d.is_dir() and d.name not in SKIP_USERS]

def clean_extensions_dir(ext_dir: Path, dry_run: bool):
    """Remove WAM extension directories. Returns list of removed dir names."""
    removed = []
    if not ext_dir.exists():
        return removed
    for pat in WAM_DIR_PATTERNS:
        for d in ext_dir.glob(pat):
            if d.is_dir():
                removed.append(d.name)
                if not dry_run:
                    shutil.rmtree(d, ignore_errors=True)
    # Also remove orphan .vsix files
    for f in ext_dir.glob('*.vsix'):
        if 'wam' in f.name.lower() or 'savior' in f.name.lower():
            removed.append(f.name)
            if not dry_run:
                f.unlink(missing_ok=True)
    return removed

def clean_extensions_json(ext_dir: Path, dry_run: bool):
    """Remove WAM entries from extensions.json. Returns count removed."""
    ej = ext_dir / 'extensions.json'
    if not ej.exists():
        return 0
    try:
        with open(ej, 'r', encoding='utf-8-sig') as f:
            arr = json.load(f)
        if not isinstance(arr, list):
            return 0
        before = len(arr)
        arr = [x for x in arr
               if x.get('identifier', {}).get('id', '') not in
               ('local.wam', 'local.wam-savior', 'local.aiswitch')]
        after = len(arr)
        diff = before - after
        if diff > 0 and not dry_run:
            with open(ej, 'w', encoding='utf-8') as f:
                json.dump(arr, f, ensure_ascii=False, separators=(',', ':'))
        return diff
    except Exception as e:
        print(f'    [WARN] extensions.json: {e}')
        return 0

def clean_state_db(user: str, dry_run: bool):
    """Remove WAM keys from state.vscdb. Returns count removed."""
    db_path = Path(f'C:/Users/{user}/AppData/Roaming/Windsurf/User/globalStorage/state.vscdb')
    if not db_path.exists():
        return 0
    try:
        # Read-only check via copy
        tmp = Path(os.environ.get('TEMP', '/tmp')) / f'_purge_{user}.vscdb'
        shutil.copy2(db_path, tmp)
        for ext in ['-wal', '-shm']:
            src = Path(str(db_path) + ext)
            if src.exists():
                shutil.copy2(src, Path(str(tmp) + ext))

        conn = sqlite3.connect(str(tmp), timeout=3)
        conditions = ' OR '.join(f"key LIKE '{p}'" for p in WAM_DB_PATTERNS)
        rows = conn.execute(f"SELECT key FROM ItemTable WHERE {conditions}").fetchall()
        conn.close()

        count = len(rows)
        if count > 0 and not dry_run:
            # Write directly to actual DB
            conn = sqlite3.connect(str(db_path), timeout=10)
            conn.execute("PRAGMA busy_timeout=5000")
            conn.execute(f"DELETE FROM ItemTable WHERE {conditions}")
            conn.commit()
            conn.close()

        # Cleanup temp
        for f in tmp.parent.glob(f'{tmp.name}*'):
            f.unlink(missing_ok=True)
        return count
    except Exception as e:
        print(f'    [WARN] state.vscdb: {e}')
        return 0

def clean_wam_hot(user: str, dry_run: bool):
    """Remove .wam-hot directory for non-owner users."""
    wam_hot = Path(f'C:/Users/{user}/.wam-hot')
    if not wam_hot.exists():
        return False
    if not dry_run:
        shutil.rmtree(wam_hot, ignore_errors=True)
    return True

def trim_log(max_lines=1000):
    """Trim zhou's wam.log to last N lines."""
    log_file = Path(f'C:/Users/{OWNER}/.wam-hot/wam.log')
    if not log_file.exists():
        return 0
    size_before = log_file.stat().st_size
    if size_before < 100_000:  # < 100KB, no trim needed
        return 0
    lines = log_file.read_text(encoding='utf-8', errors='replace').splitlines()
    if len(lines) <= max_lines:
        return 0
    trimmed = lines[-max_lines:]
    log_file.write_text('\n'.join(trimmed) + '\n', encoding='utf-8')
    size_after = log_file.stat().st_size
    return size_before - size_after

def main():
    parser = argparse.ArgumentParser(description='Purge WAM from non-zhou users')
    parser.add_argument('--execute', action='store_true', help='Actually execute (default is dry run)')
    parser.add_argument('--trim-log', action='store_true', help='Also trim zhou wam.log')
    args = parser.parse_args()
    dry_run = not args.execute

    print()
    print('=' * 60)
    print('  purge_and_isolate.py')
    print(f'  Owner: {OWNER} | Mode: {"DRY RUN" if dry_run else "EXECUTE"}')
    print('=' * 60)

    users = find_users()
    print(f'\n  Found {len(users)} user accounts')

    stats = {'dirs': 0, 'json': 0, 'db': 0, 'wamhot': 0}

    for user in sorted(users):
        is_owner = (user == OWNER)
        prefix = '[KEEP]' if is_owner else '[PURGE]'
        ext_dir = Path(f'C:/Users/{user}/.windsurf/extensions')

        if is_owner:
            print(f'\n  {prefix} {user}')
            # Only clean orphan vsix files from owner
            orphans = []
            if ext_dir.exists():
                for f in ext_dir.glob('*.vsix'):
                    if 'wam' in f.name.lower() or 'savior' in f.name.lower():
                        orphans.append(f.name)
                        if not dry_run:
                            f.unlink(missing_ok=True)
            if orphans:
                print(f'    Removed orphan: {", ".join(orphans)}')

            # Trim log if requested
            if args.trim_log:
                saved = trim_log()
                if saved > 0:
                    print(f'    Trimmed wam.log: saved {saved // 1024}KB')
            continue

        # Non-owner: full purge
        print(f'\n  {prefix} {user}')
        actions = []

        # 1. Extension dirs
        removed = clean_extensions_dir(ext_dir, dry_run)
        if removed:
            stats['dirs'] += len(removed)
            actions.append(f'dirs: {", ".join(removed)}')

        # 2. extensions.json
        n = clean_extensions_json(ext_dir, dry_run)
        if n > 0:
            stats['json'] += n
            actions.append(f'json: {n} entries')

        # 3. state.vscdb
        n = clean_state_db(user, dry_run)
        if n > 0:
            stats['db'] += n
            actions.append(f'db: {n} keys')

        # 4. .wam-hot
        if clean_wam_hot(user, dry_run):
            stats['wamhot'] += 1
            actions.append('.wam-hot')

        if actions:
            for a in actions:
                print(f'    -> {a}')
        else:
            print(f'    (clean)')

    # Summary
    total = sum(stats.values())
    print()
    print('=' * 60)
    print(f'  SUMMARY {"(DRY RUN)" if dry_run else "(EXECUTED)"}')
    print(f'  Extension dirs removed: {stats["dirs"]}')
    print(f'  extensions.json entries cleaned: {stats["json"]}')
    print(f'  state.vscdb keys removed: {stats["db"]}')
    print(f'  .wam-hot dirs removed: {stats["wamhot"]}')
    print(f'  Total actions: {total}')
    if dry_run and total > 0:
        print(f'\n  >>> Re-run with --execute to apply <<<')
    elif total == 0:
        print(f'\n  All clean. No WAM pollution found.')
    else:
        print(f'\n  Done. All non-{OWNER} users are clean.')
    print('=' * 60)
    print()

if __name__ == '__main__':
    main()
