"""The one record every check emits, and the report that carries them.

Adapters and AI judges depend only on this shape, so it is the stable contract
of the tool: changing a field here is a breaking change for every caller.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace

FAIL = "fail"
WARN = "warn"


@dataclass(frozen=True)
class Finding:
    check: str
    rule: str
    # Identity without a line number, so a finding survives code moving above
    # it. The baseline matches on this.
    key: str
    path: str
    message: str
    severity: str = FAIL
    line: int | None = None
    symbol: str | None = None
    value: float | None = None
    limit: float | None = None
    detail: str | None = None
    baselined: bool = False

    def blocks(self) -> bool:
        return self.severity == FAIL and not self.baselined

    def as_baselined(self) -> Finding:
        return replace(self, baselined=True)


class Skipped(Exception):
    """Raised by a check that could not run (tool missing, nothing configured)."""


@dataclass
class Report:
    profile: str
    mode: str
    files: int
    findings: list[Finding] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    ran: dict[str, float] = field(default_factory=dict)
    strict: bool = False

    @property
    def ok(self) -> bool:
        if self.strict and self.skipped:
            return False
        return not any(f.blocks() for f in self.findings)

    def counts(self) -> dict[str, int]:
        return {
            "fail": sum(1 for f in self.findings if f.blocks()),
            "warn": sum(1 for f in self.findings if f.severity == WARN),
            "baselined": sum(1 for f in self.findings if f.baselined),
            "skipped": len(self.skipped),
        }

    def to_json(self) -> str:
        payload = {
            "ok": self.ok,
            "profile": self.profile,
            "mode": self.mode,
            "files": self.files,
            "strict": self.strict,
            "summary": self.counts(),
            "ran": self.ran,
            "skipped": self.skipped,
            "findings": [asdict(f) for f in self.findings],
        }
        return json.dumps(payload, indent=2, sort_keys=True)

    def to_text(self) -> str:
        lines = [f"gate: {'PASS' if self.ok else 'FAIL'}  profile={self.profile}  mode={self.mode}  files={self.files}"]
        for check in sorted({f.check for f in self.findings}):
            lines.append(check)
            lines += [_line(f) for f in self.findings if f.check == check]
        for check, reason in sorted(self.skipped.items()):
            tag = "FAIL (strict)" if self.strict else "skipped"
            lines.append(f"{tag}: {check}: {reason}")
        c = self.counts()
        lines.append(f"{c['fail']} fail, {c['warn']} warn, {c['baselined']} baselined, {c['skipped']} skipped")
        return "\n".join(lines)


def _line(f: Finding) -> str:
    tag = "base" if f.baselined else ("FAIL" if f.severity == FAIL else "warn")
    where = f.path + (f":{f.line}" if f.line else "")
    symbol = f" {f.symbol}" if f.symbol else ""
    text = f"  {tag} {where}{symbol}  {f.message}"
    if f.detail and f.blocks():
        text += "\n" + "\n".join("       " + ln for ln in f.detail.splitlines())
    return text
