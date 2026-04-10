#!/usr/bin/env python3
"""
Pro Trial Only — Atomic Purge Script
=====================================
Purges Free + long-expired accounts from ALL account files simultaneously.
Handles VSIX race condition by writing multiple times with verification.
"""
import json, os, time, sys

APPDATA = os.environ.get('APPDATA', '')
GS = os.path.join(APPDATA, 'Windsurf', 'User', 'globalStorage')
now = time.time() * 1000
GRACE_MS = 3 * 86400000  # 3 days

def find_all_account_files():
    files = []
    for root, dirs, fnames in os.walk(GS):
        for f in fnames:
            if 'accounts' in f.lower() and f.endswith('.json'):
                files.append(os.path.join(root, f))
    home_bak = os.path.join(os.path.expanduser('~'), '.wam', 'accounts-backup.json')
    if os.path.exists(home_bak):
        files.append(home_bak)
    return files

def is_removable(a):
    u = a.get('usage') or {}
    plan = (u.get('plan') or '').lower()
    if plan == 'free': return 'free'
    gps = u.get('gracePeriodStatus')
    if gps is not None and gps > 1:
        bs = (u.get('billingStrategy') or '').lower()
        if not bs or bs == 'free': return 'free_degraded'
    if plan and not u.get('billingStrategy') and u.get('hasPaidFeatures') is False: return 'free_nopaid'
    pe = u.get('planEnd', 0)
    if pe and pe > 1.6e12 and (pe + GRACE_MS) < now: return 'expired'
    if a.get('_authError') in ('USER_DISABLED', 'EMAIL_NOT_FOUND'): return 'disabled'
    return None

def load_largest(files):
    best = []
    for fp in files:
        try:
            d = json.load(open(fp, 'r', encoding='utf-8'))
            if isinstance(d, list) and len(d) > len(best):
                best = d
        except: pass
    return best

def purge(accounts):
    kept = []
    removed = {}
    for a in accounts:
        reason = is_removable(a)
        if reason:
            removed[reason] = removed.get(reason, 0) + 1
        else:
            kept.append(a)
    return kept, removed

def write_all(files, data):
    json_str = json.dumps(data, indent=2, ensure_ascii=False)
    ok = 0
    for fp in files:
        try:
            d = os.path.dirname(fp)
            os.makedirs(d, exist_ok=True)
            # Atomic write: write to tmp, then rename
            tmp = fp + '.purge_tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                f.write(json_str)
            if os.path.exists(fp):
                os.remove(fp)
            os.rename(tmp, fp)
            ok += 1
        except Exception as e:
            print(f"  FAIL: {fp} ({e})")
    return ok

def verify(files, expected_count):
    verified = 0
    for fp in files:
        try:
            d = json.load(open(fp, 'r', encoding='utf-8'))
            if isinstance(d, list) and len(d) == expected_count:
                verified += 1
            else:
                print(f"  MISMATCH: {fp} has {len(d)} (expected {expected_count})")
        except Exception as e:
            print(f"  UNREADABLE: {fp} ({e})")
    return verified

if __name__ == '__main__':
    files = find_all_account_files()
    print(f"Found {len(files)} account files:")
    for fp in files:
        try:
            d = json.load(open(fp, 'r', encoding='utf-8'))
            print(f"  {fp} ({len(d)})")
        except:
            print(f"  {fp} (unreadable)")

    accounts = load_largest(files)
    print(f"\nSource: {len(accounts)} accounts")
    
    kept, removed = purge(accounts)
    total_removed = sum(removed.values())
    print(f"Purge: removed {total_removed} ({removed})")
    print(f"Kept: {len(kept)} Pro Trial accounts")

    if total_removed == 0:
        print("Nothing to purge — pool is clean!")
        sys.exit(0)

    # Write 3 times to fight VSIX race condition
    for attempt in range(3):
        written = write_all(files, kept)
        time.sleep(0.3)
        good = verify(files, len(kept))
        if good == len(files):
            print(f"\nVerified: all {good}/{len(files)} files have {len(kept)} accounts")
            break
        else:
            print(f"  Attempt {attempt+1}: {good}/{len(files)} verified, retrying...")
    
    # Final summary
    print(f"\n{'='*50}")
    print(f"Pro Trial Only Purge Complete")
    print(f"  Before: {len(accounts)}")
    print(f"  After:  {len(kept)}")
    print(f"  Free removed:    {removed.get('free',0)+removed.get('free_degraded',0)+removed.get('free_nopaid',0)}")
    print(f"  Expired removed: {removed.get('expired',0)}")
    print(f"  Disabled removed: {removed.get('disabled',0)}")
    
    # Show remaining pool health
    good_ct = exp_ct = unk_ct = 0
    for a in kept:
        u = a.get('usage') or {}
        pe = u.get('planEnd', 0)
        if pe and pe > 1.6e12:
            days = (pe - now) / 86400000
            if days > 3: good_ct += 1
            elif days > 0: exp_ct += 1
            else: exp_ct += 1  # in grace period
        else:
            unk_ct += 1
    print(f"\n  Pool health: {good_ct} good / {exp_ct} expiring / {unk_ct} unknown planEnd")
