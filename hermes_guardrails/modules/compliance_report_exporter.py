"""Generate a non-technical compliance report (HTML) from the audit log.

Reads ``~/.hermes/guardrails/audit/<project>.jsonl`` (the shared audit-log
convention from docs/module-contract.md), optionally filters by date range,
groups interception counts by rule/module, and renders a single
self-contained HTML file (inline CSS only, no external assets) with a table
of rule, count, first_seen, last_seen.

Design note: HTML was chosen deliberately over PDF for v1 because it needs
zero new dependencies (uses only the stdlib). PDF export is a stretch goal
per issue #15 and can be added later (e.g. via ``weasyprint``) as an
alternative ``fmt='pdf'`` branch without changing this module's public
contract.
"""

import html
import json
import os
from datetime import datetime, timezone


def _audit_log_path(project: str) -> str:
    return os.path.join(
        os.path.expanduser("~"), ".hermes", "guardrails", "audit", f"{project}.jsonl"
    )


def _parse_timestamp(value):
    """Best-effort parse of a timestamp field into a datetime, or None."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    return None


def _load_entries(project: str):
    """Read and parse every JSON line from the project's audit log.

    Missing file or malformed lines are handled gracefully (empty report /
    skipped line) rather than raising.
    """
    path = _audit_log_path(project)
    entries = []
    if not os.path.isfile(path):
        return entries
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entries.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    except OSError:
        return []
    return entries


def _filter_by_date(entries, start_date=None, end_date=None):
    if not start_date and not end_date:
        return entries

    start_dt = _parse_timestamp(start_date) if start_date else None
    end_dt = _parse_timestamp(end_date) if end_date else None

    filtered = []
    for entry in entries:
        ts = _parse_timestamp(entry.get("timestamp") or entry.get("ts"))
        if ts is None:
            # Can't evaluate the filter on an entry without a timestamp;
            # exclude it rather than guess.
            continue
        if start_dt is not None and ts < start_dt:
            continue
        if end_dt is not None and ts > end_dt:
            continue
        filtered.append(entry)
    return filtered


def _group_by_rule(entries):
    """Group entries by rule (falling back to module) and compute stats."""
    groups = {}
    for entry in entries:
        key = entry.get("rule") or entry.get("module") or "unknown"
        ts = _parse_timestamp(entry.get("timestamp") or entry.get("ts"))

        stats = groups.setdefault(
            key, {"count": 0, "first_seen": None, "last_seen": None}
        )
        stats["count"] += 1
        if ts is not None:
            if stats["first_seen"] is None or ts < stats["first_seen"]:
                stats["first_seen"] = ts
            if stats["last_seen"] is None or ts > stats["last_seen"]:
                stats["last_seen"] = ts
    return groups


def _fmt_dt(dt):
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S %Z").strip()


def _render_html(project, start_date, end_date, groups):
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    period = f"{start_date or 'inicio'} a {end_date or 'hoje'}"

    rows_html = ""
    for rule in sorted(groups.keys()):
        stats = groups[rule]
        rows_html += f"""
        <tr>
          <td>{html.escape(str(rule))}</td>
          <td class="num">{stats['count']}</td>
          <td>{_fmt_dt(stats['first_seen'])}</td>
          <td>{_fmt_dt(stats['last_seen'])}</td>
        </tr>"""

    if not rows_html:
        rows_html = """
        <tr>
          <td colspan="4" class="empty">Nenhuma intercepcao encontrada para este projeto/periodo.</td>
        </tr>"""

    total = sum(stats["count"] for stats in groups.values())

    return f"""<!DOCTYPE html>
<html lang="pt-br">
<head>
<meta charset="utf-8">
<title>Relatorio de Conformidade - {html.escape(project)}</title>
<style>
  body {{ font-family: Arial, Helvetica, sans-serif; margin: 2rem; color: #1a1a1a; background: #fafafa; }}
  h1 {{ font-size: 1.5rem; margin-bottom: 0.25rem; }}
  .meta {{ color: #555; font-size: 0.9rem; margin-bottom: 1.5rem; }}
  table {{ border-collapse: collapse; width: 100%; background: #fff; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
  th, td {{ padding: 0.6rem 0.8rem; border-bottom: 1px solid #e0e0e0; text-align: left; }}
  th {{ background: #2c3e50; color: #fff; font-weight: 600; }}
  td.num {{ text-align: right; font-weight: 600; }}
  td.empty {{ text-align: center; color: #888; padding: 1.5rem; }}
  tr:nth-child(even) {{ background: #f6f6f6; }}
  .summary {{ margin-top: 1rem; font-size: 0.95rem; color: #333; }}
  .footer {{ margin-top: 2rem; font-size: 0.8rem; color: #999; }}
</style>
</head>
<body>
  <h1>Relatorio de Conformidade</h1>
  <div class="meta">
    Projeto: <strong>{html.escape(project)}</strong> &nbsp;|&nbsp;
    Periodo: <strong>{html.escape(period)}</strong> &nbsp;|&nbsp;
    Gerado em: {generated_at}
  </div>
  <table>
    <thead>
      <tr>
        <th>Regra</th>
        <th>Contagem</th>
        <th>Primeira ocorrencia</th>
        <th>Ultima ocorrencia</th>
      </tr>
    </thead>
    <tbody>{rows_html}
    </tbody>
  </table>
  <div class="summary">Total de intercepcoes: <strong>{total}</strong></div>
  <div class="footer">Gerado automaticamente pelo Compliance Report Exporter (hermes-guardrails-plugin).</div>
</body>
</html>
"""


def generate_report(project: str, start_date=None, end_date=None, fmt: str = "html") -> str:
    """Generate a human-readable compliance report for ``project``.

    Reads the shared audit log, filters by ``start_date``/``end_date`` if
    given (ISO-8601 strings or datetime objects), groups interception
    counts by rule, and writes a self-contained report file.

    Returns the path to the generated report file.

    ``fmt`` only supports ``"html"`` today (self-contained, zero new
    dependencies). ``"pdf"`` is a documented stretch goal — see module
    docstring — and raises ``NotImplementedError`` for now rather than
    silently producing the wrong format.
    """
    if fmt != "html":
        raise NotImplementedError(
            "Only fmt='html' is supported currently; PDF export is a "
            "stretch goal (see module docstring) and can be added via "
            "weasyprint without changing this function's signature."
        )

    entries = _load_entries(project)
    entries = _filter_by_date(entries, start_date, end_date)
    groups = _group_by_rule(entries)

    report_html = _render_html(project, start_date, end_date, groups)

    out_dir = os.path.join(
        os.path.expanduser("~"), ".hermes", "guardrails", "reports"
    )
    os.makedirs(out_dir, exist_ok=True)

    timestamp_slug = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_path = os.path.join(out_dir, f"compliance_report_{project}_{timestamp_slug}.html")

    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(report_html)

    return out_path


MODULE = {
    "name": "compliance_report_exporter",
    # No hooks/middleware: this module is invoked directly (e.g. from a CLI
    # or scheduled job) via generate_report(), not from the guardrails
    # dispatch chain. It still follows the one-file-per-module contract.
    "hooks": {},
    "middleware": {},
}


if __name__ == "__main__":
    import shutil
    import tempfile

    _test_project = "compliance_report_exporter_selftest"
    _audit_dir = os.path.join(os.path.expanduser("~"), ".hermes", "guardrails", "audit")
    os.makedirs(_audit_dir, exist_ok=True)
    _audit_path = os.path.join(_audit_dir, f"{_test_project}.jsonl")

    _fake_lines = [
        {"timestamp": "2024-01-10T09:00:00+00:00", "rule": "scope_intent_guard", "module": "scope_intent_guard", "project": _test_project},
        {"timestamp": "2024-01-11T10:30:00+00:00", "rule": "scope_intent_guard", "module": "scope_intent_guard", "project": _test_project},
        {"timestamp": "2024-01-12T08:15:00+00:00", "rule": "secrets_filter", "module": "secrets_filter", "project": _test_project},
        {"timestamp": "2024-02-01T12:00:00+00:00", "rule": "secrets_filter", "module": "secrets_filter", "project": _test_project},
        {"timestamp": "2024-03-01T12:00:00+00:00", "rule": "cost_guard", "module": "cost_guard", "project": _test_project},
    ]

    _backup = None
    if os.path.isfile(_audit_path):
        _backup = _audit_path + ".selftest-backup"
        shutil.copy(_audit_path, _backup)

    try:
        with open(_audit_path, "w", encoding="utf-8") as fh:
            for line in _fake_lines:
                fh.write(json.dumps(line) + "\n")

        # Full range.
        out = generate_report(_test_project)
        print(f"[selftest] full report -> {out}")
        assert os.path.isfile(out)

        # Filtered range: should only pick up the January entries.
        out_filtered = generate_report(
            _test_project, start_date="2024-01-01", end_date="2024-01-31"
        )
        print(f"[selftest] filtered report -> {out_filtered}")
        assert os.path.isfile(out_filtered)

        # Non-existent project: must not crash, empty report.
        out_empty = generate_report("project_with_no_audit_log_yet")
        print(f"[selftest] empty report -> {out_empty}")
        assert os.path.isfile(out_empty)

        print("[selftest] OK")
    finally:
        try:
            os.remove(_audit_path)
        except OSError:
            pass
        if _backup and os.path.isfile(_backup):
            shutil.move(_backup, _audit_path)
