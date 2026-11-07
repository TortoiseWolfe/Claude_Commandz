#!/usr/bin/env python3
"""
skip_census.py — count every skipped, never-run and hidden-skip test in a repo

Usage:
  skip_census.py <repo-dir>                    # static code scan
  skip_census.py <repo-dir> --run <run-id>     # scan + analyze CI run artifacts
  skip_census.py <repo-dir> --pr <pr-num>      # scan + analyze PR's latest run
  skip_census.py <repo-dir> --json             # JSON output
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import List, Optional, Dict, Any


@dataclass
class SkipFinding:
    file: str
    line: int
    kind: str
    condition: str
    snippet: str


@dataclass
class RunSummary:
    ran: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    skipped_details: List[Dict[str, str]] = field(default_factory=list)
    has_json_report: bool = False


@dataclass
class CensusResult:
    repo: str
    static: Dict[str, Any] = field(default_factory=dict)
    run: Optional[Dict[str, Any]] = None


SKIP_PATTERNS = [
    (re.compile(r'\btest\.fixme\b'), 'fixme'),
    (re.compile(r'\bdescribe\.skipIf\s*\('), 'describe-skipIf'),
    (re.compile(r'\bskipIf\s*\('), 'skipIf'),
    (re.compile(r'\btest\.skip\s*\(\s*true\s*\)'), 'bare-skip-true'),
    (re.compile(r'\btest\.skip\s*\(\s*[a-zA-Z_$][a-zA-Z0-9_$]*\s*[\),]'), 'conditional-skip'),
    (re.compile(r'\b(test|describe|it)\.skip\b'), 'explicit-skip'),
]

PLAYWRIGHT_IGNORE_PATTERNS = [
    (re.compile(r'testIgnore\s*:'), 'testIgnore'),
    (re.compile(r'grepInvert\s*:'), 'grepInvert'),
]

VITEST_EXCLUDE_PATTERNS = [
    (re.compile(r'exclude\s*:'), 'vitest-exclude'),
]

HIDDEN_SKIP_PATTERNS = [
    (re.compile(r'if\s*\(\s*!process\.env\.\w+\s*\)\s*\{\s*test\.skip'), 'early-return-test-skip'),
    (re.compile(r'if\s*\(\s*!process\.env\.\w+\)\s*return'), 'early-return-process-env'),
    (re.compile(r'if\s*\(\s*![^)]+\)\s*return'), 'early-return-env'),
]

WORKFLOW_GREP_INVERT = re.compile(r'--grep-invert')


def scan_file(filepath: Path, rel_path: str) -> List[SkipFinding]:
    findings = []
    try:
        content = filepath.read_text(encoding='utf-8', errors='ignore')
    except Exception:
        return findings

    lines = content.splitlines()
    for i, line in enumerate(lines, 1):
        stripped = line.strip()

        for pattern, kind in SKIP_PATTERNS:
            if pattern.search(line):
                findings.append(SkipFinding(
                    file=rel_path,
                    line=i,
                    kind=kind,
                    condition=extract_condition(line, kind),
                    snippet=stripped[:120]
                ))
                break

        if 'playwright' in rel_path or rel_path.endswith('.config.ts') or rel_path.endswith('.config.js'):
            for pattern, kind in PLAYWRIGHT_IGNORE_PATTERNS:
                if pattern.search(line):
                    findings.append(SkipFinding(
                        file=rel_path,
                        line=i,
                        kind=kind,
                        condition=extract_condition(line, kind),
                        snippet=stripped[:120]
                    ))
                    break

        if 'vitest' in rel_path or rel_path.endswith('.config.ts') or rel_path.endswith('.config.js'):
            for pattern, kind in VITEST_EXCLUDE_PATTERNS:
                if pattern.search(line):
                    findings.append(SkipFinding(
                        file=rel_path,
                        line=i,
                        kind=kind,
                        condition=extract_condition(line, kind),
                        snippet=stripped[:120]
                    ))
                    break

        for pattern, kind in HIDDEN_SKIP_PATTERNS:
            if pattern.search(line):
                findings.append(SkipFinding(
                    file=rel_path,
                    line=i,
                    kind='possible-' + kind,
                    condition=extract_condition(line, kind),
                    snippet=stripped[:120]
                ))
                break

        if WORKFLOW_GREP_INVERT.search(line) and '.github/workflows' in rel_path:
            findings.append(SkipFinding(
                file=rel_path,
                line=i,
                kind='workflow-grep-invert',
                condition=extract_condition(line, 'grep-invert'),
                snippet=stripped[:120]
            ))

    return findings


def extract_condition(line: str, kind: str) -> str:
    if 'skipIf' in kind or 'conditional' in kind or 'grepInvert' in kind:
        m = re.search(r'\(([^)]+)\)', line)
        if m:
            return m.group(1).strip()
    if 'testIgnore' in kind or 'vitest-exclude' in kind:
        m = re.search(r':\s*([^,]+)', line)
        if m:
            return m.group(1).strip()
    if 'early-return' in kind:
        m = re.search(r'if\s*\(([^)]+)\)', line)
        if m:
            return m.group(1).strip()
    if 'grep-invert' in kind:
        m = re.search(r"--grep-invert=['\"]([^'\"]+)['\"]", line)
        if m:
            return m.group(1)
    return line.strip()[:80]


def scan_repo(repo_dir: Path) -> Dict[str, Any]:
    findings: List[SkipFinding] = []
    test_files = 0
    seen_files = set()

    for ext in ('*.ts', '*.tsx', '*.js', '*.jsx', '*.spec.ts', '*.test.ts', '*.spec.tsx', '*.test.tsx'):
        for f in repo_dir.rglob(ext):
            if any(part.startswith('.') for part in f.relative_to(repo_dir).parts):
                continue
            if 'node_modules' in f.parts or 'dist' in f.parts or 'build' in f.parts or '.git' in f.parts:
                continue
            if f in seen_files:
                continue
            seen_files.add(f)
            test_files += 1
            rel = f.relative_to(repo_dir).as_posix()
            findings.extend(scan_file(f, rel))

    for config_name in ('playwright.config.ts', 'playwright.config.js', 'vitest.config.ts', 'vitest.config.js'):
        for f in repo_dir.rglob(config_name):
            if 'node_modules' in f.parts or '.git' in f.parts:
                continue
            if f in seen_files:
                continue
            seen_files.add(f)
            rel = f.relative_to(repo_dir).as_posix()
            findings.extend(scan_file(f, rel))

    for f in repo_dir.rglob('.github/workflows/*.y*ml'):
        if '.git' in f.parts:
            continue
        if f in seen_files:
            continue
        seen_files.add(f)
        rel = f.relative_to(repo_dir).as_posix()
        findings.extend(scan_file(f, rel))

    by_kind: Dict[str, int] = {}
    for f in findings:
        by_kind[f.kind] = by_kind.get(f.kind, 0) + 1

    return {
        'test_files_scanned': test_files,
        'total_findings': len(findings),
        'by_kind': by_kind,
        'findings': [asdict(f) for f in findings]
    }


def download_run_artifacts(run_id: str, dest: Path, repo_slug: str) -> bool:
    try:
        result = subprocess.run(
            ['gh', 'run', 'download', run_id, '--repo', repo_slug, '--dir', str(dest)],
            capture_output=True, text=True, timeout=300
        )
        return result.returncode == 0
    except Exception:
        return False


def parse_playwright_json(json_path: Path) -> RunSummary:
    summary = RunSummary()
    try:
        data = json.loads(json_path.read_text())
    except Exception:
        return summary

    summary.has_json_report = True

    stats = data.get('stats', {})
    summary.ran = stats.get('expected', 0)
    summary.skipped = stats.get('skipped', 0)
    summary.passed = summary.ran - summary.skipped - stats.get('unexpected', 0) - stats.get('flaky', 0)
    summary.failed = stats.get('unexpected', 0) + stats.get('flaky', 0)

    for suite in data.get('suites', []):
        for spec in suite.get('specs', []):
            for test in spec.get('tests', []):
                for result in test.get('results', []):
                    status = result.get('status')
                    if status == 'skipped':
                        summary.skipped_details.append({
                            'title': spec.get('title', ''),
                            'reason': result.get('errors', [{}])[0].get('message', 'no reason given'),
                            'file': suite.get('file', ''),
                            'line': spec.get('line', 0)
                        })

    for suite in data.get('suites', []):
        if not suite.get('specs'):
            summary.skipped_details.append({
                'title': f"(entire suite skipped) {suite.get('file', '')}",
                'reason': 'suite has 0 specs - likely skipped via testIgnore or conditional test.skip',
                'file': suite.get('file', ''),
                'line': 0
            })

    return summary


def parse_vitest_json(json_path: Path) -> RunSummary:
    summary = RunSummary()
    try:
        data = json.loads(json_path.read_text())
    except Exception:
        return summary

    summary.has_json_report = True

    def walk_tests(node):
        if 'tasks' in node:
            for task in node['tasks']:
                walk_tests(task)
        elif 'suite' in node:
            for child in node.get('tasks', []):
                walk_tests(child)
        else:
            summary.ran += 1
            state = node.get('state')
            if state == 'pass':
                summary.passed += 1
            elif state == 'fail':
                summary.failed += 1
            elif state == 'skip':
                summary.skipped += 1
                summary.skipped_details.append({
                    'title': node.get('name', ''),
                    'reason': node.get('skipReason', 'no reason given'),
                    'file': node.get('file', ''),
                    'line': node.get('line', 0)
                })

    walk_tests(data)
    return summary


def analyze_run_artifacts(artifact_dir: Path) -> RunSummary:
    summary = RunSummary()

    for json_file in artifact_dir.rglob('*.json'):
        name = json_file.name.lower()
        if 'result' in name or 'playwright' in name or name == 'results.json':
            s = parse_playwright_json(json_file)
            if s.has_json_report:
                summary.ran += s.ran
                summary.passed += s.passed
                summary.failed += s.failed
                summary.skipped += s.skipped
                summary.skipped_details.extend(s.skipped_details)
                summary.has_json_report = True
        elif 'vitest' in name or 'test-results' in name:
            s = parse_vitest_json(json_file)
            if s.has_json_report:
                summary.ran += s.ran
                summary.passed += s.passed
                summary.failed += s.failed
                summary.skipped += s.skipped
                summary.skipped_details.extend(s.skipped_details)
                summary.has_json_report = True

    return summary


def find_run_for_pr(repo_dir: Path, pr_num: str) -> Optional[str]:
    try:
        result = subprocess.run(
            ['gh', 'run', 'list', '--repo', get_repo_slug(repo_dir), '--json', 'databaseId,headBranch',
             '--limit', '20', '--workflow', 'e2e.yml', '--workflow', 'e2e-local.yml', '--workflow', 'ci.yml'],
            capture_output=True, text=True, timeout=60, cwd=repo_dir
        )
        if result.returncode != 0:
            return None
        runs = json.loads(result.stdout)
        for run in runs:
            if pr_num in run.get('headBranch', ''):
                return str(run['databaseId'])
    except Exception:
        pass
    return None


def get_repo_slug(repo_dir: Path) -> str:
    try:
        result = subprocess.run(
            ['git', 'config', '--get', 'remote.origin.url'],
            capture_output=True, text=True, cwd=repo_dir
        )
        url = result.stdout.strip()
        if 'github.com' in url:
            if url.startswith('git@'):
                return url.split(':')[-1].replace('.git', '')
            else:
                return url.split('github.com/')[-1].replace('.git', '')
    except Exception:
        pass
    return 'unknown/unknown'


def print_human(static: Dict[str, Any], run: Optional[RunSummary], repo: str, as_json: bool):
    result = CensusResult(repo=repo, static=static)
    if run:
        result.run = {
            'ran': run.ran,
            'passed': run.passed,
            'failed': run.failed,
            'skipped': run.skipped,
            'skipped_details': run.skipped_details,
            'has_json_report': run.has_json_report
        }

    if as_json:
        print(json.dumps(asdict(result), indent=2))
        return

    print(f"\n=== Skip Census: {repo} ===")
    print(f"Test files scanned: {static['test_files_scanned']}")
    print(f"Total static findings: {static['total_findings']}")
    print()
    print("Static findings by kind:")
    for kind, count in sorted(static['by_kind'].items()):
        print(f"  {kind}: {count}")

    if run:
        print()
        print("CI Run Analysis:")
        print(f"  Ran:      {run.ran}")
        print(f"  Passed:   {run.passed}")
        print(f"  Failed:   {run.failed}")
        print(f"  Skipped:  {run.skipped}")
        if not run.has_json_report:
            print("  WARNING: No machine-readable JSON report found in artifacts.")
            print("           Repo CI needs a JSON reporter (Playwright: --reporter=json, Vitest: --reporter=json).")
        if run.skipped_details:
            print()
            print("Skipped tests (from CI artifacts):")
            for s in run.skipped_details[:20]:
                print(f"  - {s['title']}  ({s['file']}:{s['line']})  [{s['reason'][:80]}]")
            if len(run.skipped_details) > 20:
                print(f"  ... and {len(run.skipped_details) - 20} more")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('repo_dir', help='Path to repository root')
    ap.add_argument('--run', help='GitHub run ID to analyze artifacts')
    ap.add_argument('--pr', help='PR number to find and analyze its latest run')
    ap.add_argument('--json', action='store_true', help='Output JSON')
    args = ap.parse_args(argv)

    repo_dir = Path(args.repo_dir).resolve()
    if not repo_dir.exists():
        print(f"skip_census: {repo_dir} does not exist", file=sys.stderr)
        return 2

    repo_slug = get_repo_slug(repo_dir)
    static = scan_repo(repo_dir)

    run_summary = None
    if args.pr:
        run_id = find_run_for_pr(repo_dir, args.pr)
        if not run_id:
            print(f"skip_census: could not find run for PR #{args.pr}", file=sys.stderr)
            return 1
        args.run = run_id

    if args.run:
        with tempfile.TemporaryDirectory() as tmp:
            artifact_dir = Path(tmp) / 'artifacts'
            artifact_dir.mkdir()
            print(f"Downloading artifacts for run {args.run}...", file=sys.stderr)
            if download_run_artifacts(args.run, artifact_dir, repo_slug):
                run_summary = analyze_run_artifacts(artifact_dir)
            else:
                print(f"skip_census: failed to download artifacts for run {args.run}", file=sys.stderr)
                return 1

    print_human(static, run_summary, repo_slug, args.json)
    return 0


if __name__ == '__main__':
    sys.exit(main())