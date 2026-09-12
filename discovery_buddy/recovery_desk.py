from __future__ import annotations

import argparse
import hashlib
import html
import json
from pathlib import Path
import shlex
import sys
from typing import Any

REPORT_SCHEMA = "axm.discovery-recovery-preview/v0.1"
READY_STATUSES = {"READY_FINALIZE_NEW", "READY_ROLLBACK_LAST_GOOD"}


def _digest_label(current: str | None, *, new_sha256: str, old_sha256: str | None) -> str:
    if current is None:
        return "MISSING"
    if current == new_sha256:
        return "MATCHES_NEW"
    if old_sha256 is not None and current == old_sha256:
        return "MATCHES_LAST_GOOD"
    return "OTHER_BYTES"


def _preview_recovery(output_dir: Path, public: bool) -> dict[str, Any]:
    # Import the recovery implementation itself so the preview shares its exact journal admission
    # and digest/path rules instead of maintaining a second interpretation of transaction truth.
    from . import cli as recovery_truth

    json_path, md_path = recovery_truth._paths(output_dir, public)
    journal_path = recovery_truth._transaction_path(json_path)
    mode = "PUBLIC_SAFE" if public else "LOCAL_ONLY"
    command = [
        sys.executable,
        "-m",
        "discovery_buddy",
        "recover",
        "--output-dir",
        str(output_dir),
    ]
    if public:
        command.append("--public")

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "mode": mode,
        "status": "CLEAR",
        "status_title": "NO RECOVERY NEEDED",
        "summary": "No interrupted discovery publication transaction is visible for this output pair.",
        "output_pair": [json_path.name, md_path.name],
        "journal": journal_path.name,
        "targets": [],
        "recovery_command_argv": command,
        "recovery_command_posix": shlex.join(command),
        "recovery_effect": "No recovery action is indicated by this snapshot.",
        "error": None,
        "authority": {
            "display_only": True,
            "mutates_discovery_outputs": False,
            "executes_recovery": False,
            "automatic_authority": False,
        },
        "truth_ceiling": (
            "Snapshot only. This preview never applies recovery. The production recover command "
            "re-reads and re-validates transaction evidence before any output mutation."
        ),
    }

    if not journal_path.exists() and not journal_path.is_symlink():
        return report

    try:
        journal_digest_before = recovery_truth._sha256_regular(journal_path)
        transaction = recovery_truth._load_transaction(journal_path, (json_path, md_path))
        parent = journal_path.parent
        target_rows: list[dict[str, Any]] = []
        rollback_ready = True

        for target, entry in zip((json_path, md_path), transaction["targets"]):
            current_sha = recovery_truth._target_digest(target)
            old_sha = entry["old_sha256"]
            backup_name = entry.get("backup")
            backup_state = "NOT_REQUIRED"
            backup_sha: str | None = None
            if old_sha is not None:
                backup = recovery_truth._safe_artifact(parent, backup_name)
                try:
                    backup_sha = recovery_truth._sha256_regular(backup)
                except OSError:
                    backup_state = "INVALID_OR_MISSING"
                    rollback_ready = False
                else:
                    backup_state = "MATCHES_LAST_GOOD" if backup_sha == old_sha else "DIGEST_MISMATCH"
                    if backup_sha != old_sha:
                        rollback_ready = False

            stage_name = entry.get("stage")
            stage_path = recovery_truth._safe_artifact(parent, stage_name)
            stage_present = stage_path.is_file() and not stage_path.is_symlink()

            target_rows.append(
                {
                    "name": target.name,
                    "current_state": _digest_label(
                        current_sha,
                        new_sha256=entry["new_sha256"],
                        old_sha256=old_sha,
                    ),
                    "current_sha256": current_sha,
                    "new_sha256": entry["new_sha256"],
                    "last_good_sha256": old_sha,
                    "backup": backup_name,
                    "backup_state": backup_state,
                    "backup_sha256": backup_sha,
                    "stage": stage_name,
                    "stage_present": stage_present,
                }
            )

        journal_digest_after = recovery_truth._sha256_regular(journal_path)
        second_target_digests = [recovery_truth._target_digest(path) for path in (json_path, md_path)]
        first_target_digests = [row["current_sha256"] for row in target_rows]
        if journal_digest_before != journal_digest_after or first_target_digests != second_target_digests:
            report.update(
                {
                    "status": "HELD_CHANGED_DURING_PREVIEW",
                    "status_title": "STATE CHANGED DURING PREVIEW",
                    "summary": (
                        "The recovery evidence changed while it was being inspected. No recovery "
                        "outcome is inferred from this unstable snapshot."
                    ),
                    "recovery_effect": "Re-run the preview after the active publication/recovery activity settles.",
                    "targets": target_rows,
                }
            )
            return report

        report["targets"] = target_rows
        finals_are_new = all(row["current_state"] == "MATCHES_NEW" for row in target_rows)
        if finals_are_new:
            report.update(
                {
                    "status": "READY_FINALIZE_NEW",
                    "status_title": "NEW GENERATION COMPLETE",
                    "summary": (
                        "Both final discovery files match the journal-bound new generation. "
                        "Recovery is expected to keep these final bytes and clear transaction artifacts."
                    ),
                    "recovery_effect": (
                        "Expected production outcome: FINALIZED_NEW. Final JSON/Markdown bytes should stay unchanged; "
                        "journal/staging/backup artifacts are cleaned after re-validation."
                    ),
                }
            )
            return report

        if rollback_ready:
            report.update(
                {
                    "status": "READY_ROLLBACK_LAST_GOOD",
                    "status_title": "ROLLBACK EVIDENCE READY",
                    "summary": (
                        "The final pair is not the complete new generation, and every required last-good backup "
                        "matches its journal-bound SHA-256. Recovery is expected to restore the exact last-good pair."
                    ),
                    "recovery_effect": (
                        "Expected production outcome: ROLLED_BACK_LAST_GOOD. Recovery re-validates every required "
                        "backup before changing either final output."
                    ),
                }
            )
            return report

        report.update(
            {
                "status": "HELD_INVALID_EVIDENCE",
                "status_title": "RECOVERY EVIDENCE HELD",
                "summary": (
                    "The final pair is not the complete new generation, but at least one required last-good backup "
                    "is missing, unsafe, or does not match its journal-bound SHA-256."
                ),
                "recovery_effect": (
                    "No safe rollback is inferred. The production recover command is expected to fail closed "
                    "unless the required evidence is restored or independently investigated."
                ),
            }
        )
        return report
    except OSError as exc:
        report.update(
            {
                "status": "HELD_INVALID_EVIDENCE",
                "status_title": "RECOVERY EVIDENCE HELD",
                "summary": "The transaction evidence could not be admitted by the existing recovery rules.",
                "recovery_effect": "No recovery outcome is inferred from invalid evidence.",
                "error": f"{exc.__class__.__name__}: {exc}",
            }
        )
        return report


def inspect_recovery(output_dir: str | Path, *, public: bool = False) -> dict[str, Any]:
    return _preview_recovery(Path(output_dir), public)


def _short_digest(value: str | None) -> str:
    return "—" if value is None else f"{value[:12]}…{value[-8:]}"


def render_text(report: dict[str, Any]) -> str:
    lines = [
        "AXM DISCOVERY RECOVERY DESK",
        f"{report['mode']} · DISPLAY ≠ RECOVERY AUTHORITY",
        "",
        report["status_title"],
        report["summary"],
    ]
    if report.get("error"):
        lines.extend(["", f"Held reason: {report['error']}"])
    if report.get("targets"):
        lines.extend(["", "OUTPUT PAIR"])
        for row in report["targets"]:
            lines.append(
                f"- {row['name']}: {row['current_state']} · current {_short_digest(row['current_sha256'])} "
                f"· new {_short_digest(row['new_sha256'])} · last-good {_short_digest(row['last_good_sha256'])} "
                f"· backup {row['backup_state']}"
            )
    lines.extend(["", "RECOVERY EFFECT", report["recovery_effect"]])
    if report["status"] in READY_STATUSES:
        lines.extend(["", "NEXT ACTION", report["recovery_command_posix"]])
    elif report["status"] == "HELD_CHANGED_DURING_PREVIEW":
        lines.extend(["", "NEXT ACTION", "Re-run this preview after current output activity settles."])
    elif report["status"] == "HELD_INVALID_EVIDENCE":
        lines.extend(["", "NEXT ACTION", "Investigate the held transaction evidence; do not infer a safe mutation from this preview."])
    lines.extend(["", "TRUTH CEILING", report["truth_ceiling"]])
    return "\n".join(lines) + "\n"


def _status_tone(status: str) -> str:
    if status == "READY_ROLLBACK_LAST_GOOD":
        return "warn"
    if status == "READY_FINALIZE_NEW":
        return "good"
    if status.startswith("HELD_"):
        return "held"
    return "quiet"


def render_html(report: dict[str, Any]) -> str:
    esc = lambda value: html.escape(str(value), quote=True)
    tone = _status_tone(report["status"])
    target_cards = []
    for row in report.get("targets", []):
        target_cards.append(
            f"""
            <article class="target-card" data-target-state="{esc(row['current_state'])}">
              <div class="target-head">
                <h3>{esc(row['name'])}</h3>
                <span class="state-chip">{esc(row['current_state'].replace('_', ' '))}</span>
              </div>
              <dl>
                <div><dt>Current</dt><dd title="{esc(row['current_sha256'] or 'missing')}">{esc(_short_digest(row['current_sha256']))}</dd></div>
                <div><dt>Journal new</dt><dd title="{esc(row['new_sha256'])}">{esc(_short_digest(row['new_sha256']))}</dd></div>
                <div><dt>Last good</dt><dd title="{esc(row['last_good_sha256'] or 'none')}">{esc(_short_digest(row['last_good_sha256']))}</dd></div>
                <div><dt>Rollback backup</dt><dd>{esc(row['backup_state'].replace('_', ' '))}</dd></div>
              </dl>
            </article>
            """
        )
    targets_html = "".join(target_cards) or '<p class="empty">No interrupted transaction targets are present in this snapshot.</p>'
    error_html = ""
    if report.get("error"):
        error_html = f'<p class="held-reason"><strong>Held reason</strong><span>{esc(report["error"])}</span></p>'

    command_html = ""
    if report["status"] in READY_STATUSES:
        command = esc(report["recovery_command_posix"])
        command_html = f"""
        <section class="action-panel" aria-labelledby="next-action-title">
          <div>
            <p class="eyebrow">NEXT ACTION</p>
            <h2 id="next-action-title">Recovery stays explicit</h2>
            <p>Run the existing production recovery command only when you choose to proceed. It re-validates the evidence before mutation.</p>
          </div>
          <code id="recovery-command">{command}</code>
          <button id="copy-command" type="button">Copy recovery command</button>
          <span id="copy-status" class="copy-status" role="status" aria-live="polite"></span>
        </section>
        """
    elif report["status"] == "HELD_CHANGED_DURING_PREVIEW":
        command_html = """
        <section class="action-panel held-action" aria-labelledby="next-action-title">
          <div><p class="eyebrow">NEXT ACTION</p><h2 id="next-action-title">Preview again after activity settles</h2>
          <p>This snapshot changed while it was being read, so it deliberately offers no recovery command.</p></div>
        </section>
        """
    elif report["status"] == "HELD_INVALID_EVIDENCE":
        command_html = """
        <section class="action-panel held-action" aria-labelledby="next-action-title">
          <div><p class="eyebrow">NEXT ACTION</p><h2 id="next-action-title">Investigate before mutation</h2>
          <p>The desk cannot prove a safe recovery path from this evidence, so it deliberately offers no recovery command.</p></div>
        </section>
        """

    report_json = esc(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AXM Discovery Recovery Desk · {esc(report['status_title'])}</title>
<style>
:root {{ color-scheme: dark; --ink:#eef7ff; --muted:#93a6ba; --line:rgba(189,219,241,.18); --panel:rgba(10,19,29,.82); --panel2:rgba(20,33,46,.72); --accent:#8bd9ff; --warn:#ffd27a; --good:#90f0c0; --held:#ff9d9d; }}
* {{ box-sizing:border-box; }}
html {{ min-width:0; background:#050a0f; }}
body {{ margin:0; min-width:0; min-height:100vh; color:var(--ink); font:15px/1.55 ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; background:radial-gradient(circle at 12% 0%,rgba(74,135,177,.23),transparent 36rem),linear-gradient(145deg,#07111a,#04080c 64%); }}
body::before {{ content:""; position:fixed; inset:0; pointer-events:none; opacity:.18; background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),linear-gradient(90deg,rgba(255,255,255,.025) 1px,transparent 1px); background-size:32px 32px; mask-image:linear-gradient(to bottom,black,transparent 75%); }}
main {{ width:min(1040px,calc(100% - 32px)); margin:0 auto; padding:38px 0 64px; position:relative; }}
.topline {{ display:flex; flex-wrap:wrap; gap:8px 18px; align-items:center; justify-content:space-between; color:var(--muted); font-size:.78rem; letter-spacing:.12em; text-transform:uppercase; }}
.brand {{ color:var(--ink); font-weight:800; }}
.hero {{ margin-top:22px; border:1px solid var(--line); border-radius:26px; padding:clamp(22px,4vw,42px); background:linear-gradient(145deg,rgba(16,31,44,.94),rgba(8,15,23,.87)); box-shadow:0 28px 80px rgba(0,0,0,.32),inset 0 1px rgba(255,255,255,.04); overflow:hidden; position:relative; }}
.hero::after {{ content:""; position:absolute; width:240px; height:240px; border-radius:50%; right:-110px; top:-120px; border:1px solid currentColor; opacity:.14; box-shadow:0 0 70px currentColor; }}
.hero.warn {{ color:var(--warn); }} .hero.good {{ color:var(--good); }} .hero.held {{ color:var(--held); }} .hero.quiet {{ color:var(--accent); }}
.eyebrow {{ margin:0 0 7px; color:currentColor; font-size:.74rem; font-weight:800; letter-spacing:.16em; text-transform:uppercase; }}
h1 {{ margin:0; max-width:16ch; color:var(--ink); font-size:clamp(2rem,7vw,4.7rem); line-height:.98; letter-spacing:-.045em; }}
.hero-copy {{ max-width:70ch; margin:18px 0 0; color:#bed0df; font-size:clamp(1rem,2.3vw,1.15rem); }}
.held-reason {{ display:grid; gap:4px; margin:18px 0 0; padding:13px 15px; border:1px solid rgba(255,157,157,.28); border-radius:14px; background:rgba(90,21,25,.18); color:#ffd2d2; }}
.section {{ margin-top:26px; }}
.section-head {{ display:flex; flex-wrap:wrap; align-items:end; justify-content:space-between; gap:12px; margin:0 2px 12px; }}
.section h2,.action-panel h2 {{ margin:0; font-size:1.12rem; letter-spacing:-.01em; }}
.section-note {{ color:var(--muted); font-size:.85rem; }}
.target-grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:14px; }}
.target-card,.effect-card,.action-panel,.truth-card {{ min-width:0; border:1px solid var(--line); border-radius:20px; background:linear-gradient(145deg,var(--panel),var(--panel2)); box-shadow:inset 0 1px rgba(255,255,255,.035); }}
.target-card {{ padding:18px; }}
.target-head {{ display:flex; gap:12px; align-items:flex-start; justify-content:space-between; }}
.target-head h3 {{ margin:0; min-width:0; overflow-wrap:anywhere; font-size:.98rem; }}
.state-chip {{ flex:none; max-width:60%; padding:5px 8px; border:1px solid var(--line); border-radius:999px; color:#cbe9fb; background:rgba(72,142,184,.11); font-size:.67rem; font-weight:800; letter-spacing:.07em; text-transform:uppercase; text-align:center; }}
dl {{ display:grid; gap:9px; margin:18px 0 0; }}
dl div {{ min-width:0; display:grid; grid-template-columns:7.4rem minmax(0,1fr); gap:10px; align-items:baseline; border-top:1px solid rgba(189,219,241,.1); padding-top:9px; }}
dt {{ color:var(--muted); font-size:.76rem; text-transform:uppercase; letter-spacing:.07em; }}
dd {{ margin:0; min-width:0; color:#dceaf5; font:600 .8rem/1.45 ui-monospace,SFMono-Regular,Consolas,monospace; overflow-wrap:anywhere; text-align:right; }}
.effect-card {{ padding:20px; color:#c9d8e4; }}
.effect-card p {{ margin:0; }}
.action-panel {{ margin-top:26px; padding:20px; display:grid; grid-template-columns:minmax(0,1fr) auto; gap:14px 18px; align-items:center; }}
.action-panel > div {{ grid-column:1/-1; }}
.action-panel p {{ margin:6px 0 0; color:var(--muted); }}
.action-panel code {{ min-width:0; padding:14px 15px; border:1px solid rgba(139,217,255,.2); border-radius:13px; background:#02070b; color:#cdeeff; overflow-wrap:anywhere; font:600 .82rem/1.45 ui-monospace,SFMono-Regular,Consolas,monospace; }}
button {{ min-height:44px; min-width:44px; border:1px solid rgba(139,217,255,.33); border-radius:12px; padding:10px 14px; color:#effaff; background:linear-gradient(#17374a,#102837); font:700 .82rem/1 ui-sans-serif,system-ui,sans-serif; cursor:pointer; }}
button:hover {{ filter:brightness(1.12); }} button:focus-visible {{ outline:3px solid #b6eaff; outline-offset:3px; }}
.copy-status {{ grid-column:1/-1; min-height:1.4em; color:#a8bed0; font-size:.82rem; }}
.held-action {{ grid-template-columns:1fr; }}
.truth-card {{ margin-top:14px; padding:18px 20px; display:grid; grid-template-columns:auto minmax(0,1fr); gap:13px; align-items:start; }}
.truth-mark {{ width:34px; height:34px; display:grid; place-items:center; border:1px solid rgba(139,217,255,.25); border-radius:10px; color:var(--accent); font-weight:900; }}
.truth-card p {{ margin:1px 0 0; color:#aebfce; }}
.empty {{ margin:0; padding:20px; border:1px dashed var(--line); border-radius:18px; color:var(--muted); }}
details {{ margin-top:14px; border:1px solid var(--line); border-radius:15px; background:rgba(5,10,15,.56); }}
summary {{ min-height:44px; display:flex; align-items:center; padding:9px 14px; color:#bcd0df; cursor:pointer; font-weight:700; }}
pre {{ margin:0; max-height:22rem; overflow:auto; border-top:1px solid var(--line); padding:15px; color:#9fb4c5; font:12px/1.55 ui-monospace,SFMono-Regular,Consolas,monospace; white-space:pre-wrap; overflow-wrap:anywhere; }}
@media (max-width:650px) {{ main {{ width:min(100% - 20px,1040px); padding-top:20px; }} .hero {{ border-radius:20px; padding:22px 18px; }} .target-grid {{ grid-template-columns:1fr; }} .action-panel {{ grid-template-columns:1fr; }} .action-panel code,.action-panel button {{ grid-column:1; }} .action-panel button {{ width:100%; }} dl div {{ grid-template-columns:1fr; gap:2px; }} dd {{ text-align:left; }} .truth-card {{ grid-template-columns:1fr; }} .topline {{ font-size:.69rem; }} }}
@media (prefers-reduced-motion:reduce) {{ *,*::before,*::after {{ scroll-behavior:auto!important; transition:none!important; animation:none!important; }} }}
@media (prefers-contrast:more) {{ :root {{ --line:rgba(225,242,255,.45); --muted:#bdcad5; }} .target-card,.effect-card,.action-panel,.truth-card,.hero {{ background:#07111a; }} }}
</style>
</head>
<body data-recovery-status="{esc(report['status'])}">
<main>
  <div class="topline"><span class="brand">AXM DISCOVERY · RECOVERY DESK</span><span>{esc(report['mode'])} · LOCAL / OFFLINE · DISPLAY ≠ RECOVERY AUTHORITY</span></div>
  <section class="hero {tone}" aria-labelledby="status-title">
    <p class="eyebrow">RECOVERY PREVIEW</p>
    <h1 id="status-title">{esc(report['status_title'])}</h1>
    <p class="hero-copy">{esc(report['summary'])}</p>
    {error_html}
  </section>

  <section class="section" aria-labelledby="output-pair-title">
    <div class="section-head"><div><p class="eyebrow">EXACT EVIDENCE</p><h2 id="output-pair-title">Output pair</h2></div><span class="section-note">{esc(report['journal'])}</span></div>
    <div class="target-grid">{targets_html}</div>
  </section>

  <section class="section" aria-labelledby="effect-title">
    <div class="section-head"><div><p class="eyebrow">EXPECTED EFFECT</p><h2 id="effect-title">What recovery would attempt</h2></div></div>
    <div class="effect-card"><p>{esc(report['recovery_effect'])}</p></div>
  </section>

  {command_html}

  <aside class="truth-card" aria-label="Truth ceiling"><div class="truth-mark" aria-hidden="true">≠</div><div><p class="eyebrow">TRUTH CEILING</p><p>{esc(report['truth_ceiling'])}</p></div></aside>
  <details><summary>Inspect exact preview receipt</summary><pre>{report_json}</pre></details>
</main>
<script>
const copyButton = document.getElementById('copy-command');
if (copyButton) {{
  copyButton.addEventListener('click', async () => {{
    const command = document.getElementById('recovery-command');
    const status = document.getElementById('copy-status');
    const text = command?.textContent || '';
    try {{
      if (!navigator.clipboard?.writeText) throw new Error('clipboard unavailable');
      await navigator.clipboard.writeText(text);
      status.textContent = 'Recovery command copied.';
    }} catch (_error) {{
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(command);
      selection.removeAllRanges();
      selection.addRange(range);
      status.textContent = 'Clipboard unavailable here — command selected for manual copy.';
    }}
  }});
}}
</script>
</body>
</html>
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m discovery_buddy.recovery_desk",
        description="Read-only human preview of one Discovery Buddy recovery transaction",
    )
    parser.add_argument("--output-dir", default=".discovery")
    parser.add_argument("--public", action="store_true", help="inspect the public-safe output pair")
    parser.add_argument("--html", help="write a self-contained local HTML recovery preview")
    parser.add_argument("--json", action="store_true", help="print the exact preview receipt as JSON instead of the human text view")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    report = inspect_recovery(args.output_dir, public=args.public)
    if args.html:
        html_path = Path(args.html)
        html_path.parent.mkdir(parents=True, exist_ok=True)
        html_path.write_text(render_html(report), "utf-8")
    if args.json:
        print(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2))
    else:
        print(render_text(report), end="")
    return 2 if report["status"].startswith("HELD_") else 0


if __name__ == "__main__":
    raise SystemExit(main())
