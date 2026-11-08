"""One-time data remediation for the paper-trading home (P1.2 + P1.4 migration).

  * archives the contaminated v1 journal/diary (8 replay rows inside the LIVE
    trades.csv -- audit finding H6) into <home>/archive/pre_remediation_2026-11-05/
  * rewrites the live journal with the v2 schema header (live rows only: none
    exist yet, state.json counts zero live trades)
  * migrates state.json to schema v2 (drops the rewound Nov-2 day state,
    seeds last_bar_ts from done_bars, adds best_day/best-day tracking fields)

Re-run safety (post-audit finding N5): the first version claimed to be
idempotent but a second run would have overwritten the pre-remediation
archive with already-clean files. Every destructive step is now GUARDED:
  step 1 (archive)  -- skipped entirely if a complete archive already exists
                       (the pre-remediation originals are never overwritten);
  step 2 (journal)  -- skipped if the header is already the v2 schema;
  step 3 (diary)    -- skipped if the diary already starts with the v2 header;
  step 4 (migration)-- idempotent by construction (schema/setdefault based).
The verification step adapts: zero-trade assertions run only on a first run.

Paths are overridable for isolated testing (defaults unchanged):
  REM_HOME, REM_BACKUP, REM_ARCHIVE, REM_TODAY
Production (Tradovate/NT8) files are NOT touched -- this operates only on
the paper-trading research home.
"""
import json
import os
import shutil
import sys

ROOT = "/home/z/my-project"
HOME = os.environ.get("REM_HOME", f"{ROOT}/download/paper_trading")
BACKUP = os.environ.get("REM_BACKUP", f"{ROOT}/remediation_workspace/backup_live_data_2026-11-05")
ARCHIVE = os.environ.get("REM_ARCHIVE", f"{HOME}/archive/pre_remediation_2026-11-05")
TODAY = os.environ.get("REM_TODAY", "2026-11-05")

sys.path.insert(0, f"{ROOT}/scripts")
import paper_step as ps  # noqa: E402  (for migrate_state only; paths untouched)

ARCHIVE_FILES = ("state.json", "trades.csv", "equity.csv", "log.md")
V2_DIARY_PREFIX = "# VWAP Fade paper-trading diary (engine v2)"


def main():
    # 0) safety: require the backup taken before any edit
    for f in ARCHIVE_FILES:
        assert os.path.exists(f"{BACKUP}/{f}"), f"backup missing: {BACKUP}/{f}"
    print("backup verified:", BACKUP)

    # 1) archive originals -- NEVER overwrite an existing complete archive (N5)
    os.makedirs(ARCHIVE, exist_ok=True)
    have = [f for f in ARCHIVE_FILES if os.path.exists(f"{ARCHIVE}/{f}")]
    if len(have) == len(ARCHIVE_FILES):
        print(f"archive already complete -> NOT overwritten "
              f"(pre-remediation originals preserved): {ARCHIVE}")
    else:
        if have:
            print(f"WARNING: partial archive found ({have}) -- completing it")
        for f in ARCHIVE_FILES:
            shutil.copy2(f"{HOME}/{f}", f"{ARCHIVE}/{f}")
        with open(f"{ARCHIVE}/README.txt", "w") as fh:
            fh.write(
                "Archived 2026-11-05 during P0/P1 remediation (task: REMEDIATE VWAP FADE\n"
                "PAPER-TRADING SYSTEM).\n\n"
                "trades.csv here contains 8 rows, ALL mode=replay: these were written by\n"
                "the 2026-11-04 15:42 ET --replay smoke test into the LIVE journal (audit\n"
                "finding H6 -- replay/live records were not separated in engine v1). The\n"
                "live state.json counts 0 live trades, so no genuine paper-trade history\n"
                "was lost by starting the v2 journal empty.\n\n"
                "log.md here is the v1 diary (replay lines + Nov-4 day summary).\n"
                "state.json here is the v1 state (day entries rewound to 2026-11-02 by\n"
                "the stale-feed reprocessing defect H4; done_bars covering Nov 3-4).\n"
                "equity.csv (1 live mark) was preserved unchanged in the live home.\n\n"
                "A full pre-remediation copy also lives in\n"
                "remediation_workspace/backup_live_data_2026-11-05/.\n")
        print("archived ->", ARCHIVE)

    # 2) rewrite the live journal: v2 header, live rows only (skip if already v2)
    import csv
    with open(f"{HOME}/trades.csv") as fh:
        rows = list(csv.reader(fh))
    header, body = rows[0], rows[1:]
    if header == list(ps.JOURNAL_COLS):
        live_rows = [r for r in body if r and r[0] == "live"]
        print(f"journal header already v2 -> rewrite skipped "
              f"({len(live_rows)} live rows, {len(body) - len(live_rows)} non-live rows present)")
    else:
        live_rows = [r for r in body if r and r[0] == "live"]
        replay_rows = [r for r in body if r and r[0] == "replay"]
        with open(f"{HOME}/trades.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(ps.JOURNAL_COLS)
            w.writerows(live_rows)
        print(f"journal: kept {len(live_rows)} live rows; archived {len(replay_rows)} replay "
              f"rows; header is now the v2 schema ({len(ps.JOURNAL_COLS)} columns)")

    # 3) fresh diary (only if it is still a v1-era diary)
    with open(f"{HOME}/log.md") as fh:
        first_line = fh.readline().rstrip("\n")
    if first_line.startswith(V2_DIARY_PREFIX):
        print("diary already v2 -> restart skipped (current diary preserved)")
    else:
        with open(f"{HOME}/log.md", "w") as fh:
            fh.write(
                "# VWAP Fade paper-trading diary (engine v2)\n\n"
                f"Diary restarted {TODAY} during remediation: the v1 diary mixed replay and\n"
                "live lines (audit H6) and is archived under archive/pre_remediation_2026-11-05/.\n"
                "Engine v2 writes replay output to replay/ only; live diary lines carry unique\n"
                "run delimiters and journal rows carry unique trade_ids.\n\n")
        print("diary restarted (v1 diary archived)")

    # 4) state migration to schema v2 (idempotent by construction)
    with open(f"{HOME}/state.json") as fh:
        st = json.load(fh)
    first_run_marker = st.get("schema_version") != 2
    before_day = {k: v["date"] for k, v in st.get("day", {}).items()}
    changed = ps.migrate_state(st, TODAY)
    with open(f"{HOME}/state.json.tmp", "w") as fh:
        json.dump(st, fh, indent=1, default=str)
    os.replace(f"{HOME}/state.json.tmp", f"{HOME}/state.json")
    print(f"state: day entries before migration: {before_day}")
    print("state migration changes:", "; ".join(changed) if changed else "none (already v2)")
    print(f"state now: schema={st['schema_version']}, cum={st['cum']}, n_trades={st['n_trades']}, "
          f"halted_date={st.get('halted_date')}, last_bar_ts={ {k: v[:16] for k, v in st['last_bar_ts'].items()} }, "
          f"day keys={list(st['day'].keys())}")

    # 5) verification (zero-trade assertions only on a first run)
    with open(f"{HOME}/state.json") as fh:
        v = json.load(fh)
    assert v["schema_version"] == 2
    assert all(d["date"] == TODAY or d.get("pos") for d in v.get("day", {}).values())
    if first_run_marker:
        assert v["cum"] == 0.0 and v["n_trades"] == 0
    with open(f"{HOME}/trades.csv") as fh:
        first = fh.readline().strip()
    assert first == ",".join(ps.JOURNAL_COLS), "journal header mismatch"
    assert os.path.exists(f"{ARCHIVE}/trades.csv") and os.path.exists(f"{ARCHIVE}/log.md")
    print("VERIFICATION PASSED: journal header (v2), state v2 + no stale day entries, "
          "backups + archive in place")


if __name__ == "__main__":
    main()
