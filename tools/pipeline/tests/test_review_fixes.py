"""Offline regressions for pipeline review findings."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from fakesheet import SCRIPTS
sys.path.insert(0, str(SCRIPTS))
import daily_pipeline
import notion_sync
import sheets_api


class ReviewFixes(unittest.TestCase):
    def test_dry_ledger_never_enters_production_history(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(daily_pipeline, 'DATA', Path(tmp)):
            dry = daily_pipeline.Ledger('2026-11-01', dry_run=True)
            dry.step('search')['done'] = True
            dry.job('job')['cv'] = {'dry_run': True}
            dry.save()
            self.assertEqual(daily_pipeline.job_history('job'), (0, {}))
            self.assertIsNone(daily_pipeline.last_search_day())
            self.assertEqual(daily_pipeline.Ledger('2026-11-01').d['steps'], {})

    def test_missing_headers_append_after_last_occupied_column(self):
        headers = [c[1] for c in sheets_api.PIPE_COLS[:-1]]
        headers.insert(1, '')
        headers += ['', '']
        tab = Mock(grid=[headers])
        tab.cell.side_effect = lambda r, c: {"formattedValue": headers[c - 1]}
        book = Mock()
        book.tab.return_value = tab
        sheets_api.Backend(book).ptab(sheets_api.PIPE_NAME)
        book.set_row.assert_called_once_with(tab, 1, len(headers) - 1, ['Notion'])

    def test_notion_cleared_fields_are_explicit_updates(self):
        row = {'url': 'https://example.com/job', 'company': 'Co', 'note': 'note',
               'city': 'Berlin', 'score': 7, 'type': 'AI agent', 'country': '德國',
               'mode': 'Remote', 'date': '2026-11-01'}
        old = notion_sync.properties('每日職缺', row)
        state = {'每日職缺': {notion_sync.url_key(row['url']): {'page': 'page', 'fp': notion_sync.fingerprint(old)}}}
        cleared = {k: v for k, v in row.items() if k in ('url', 'company')}
        op, = notion_sync.plan('每日職缺', [cleared], state, {})
        self.assertEqual(op['op'], 'update')
        for name in ('備註', '城市', '類型', '國家', '工作型態', 'date:日期:start', '評分理由', '處理狀態', '投遞紀錄'):
            self.assertEqual(op['properties'][name], '')
        self.assertIsNone(op['properties']['評分'])
        state['每日職缺'][op['key']]['fp'] = op['fp']
        self.assertEqual(notion_sync.plan('每日職缺', [cleared], state, {}), [])

    def test_scheduler_preserves_python_exit_status(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = root / 'tools/pipeline/run_daily.sh'
            script.parent.mkdir(parents=True)
            script.write_text((SCRIPTS.parents[3] / 'tools/pipeline/run_daily.sh').read_text())
            binary = root / 'bin'
            binary.mkdir()
            python = binary / 'python3'
            python.write_text('#!/bin/sh\nexit "$FAKE_EXIT"\n')
            python.chmod(0o755)
            for code in (0, 7):
                result = subprocess.run(['bash', str(script)], env={**os.environ, 'PATH': str(binary) + ':' + os.environ['PATH'], 'FAKE_EXIT': str(code)})
                self.assertEqual(result.returncode, code)
                self.assertIn(f'scheduler end (exit {code})', (root / 'data/pipeline/logs/scheduler.log').read_text())
