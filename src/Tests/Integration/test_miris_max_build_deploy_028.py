# Copyright 2026 Miris Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
"""
MAX-BUILD-DEPLOY-028 -- a failure in a project that ships nothing must not stop the plugin
being packaged, and a run that packages nothing must say so unmissably.

THE DEFECT, measured on the box 2026-09-08. The installed plugin was:

    maxUsd.dll               2026-08-21 17:25
    MaxUsd_Translators.dll   2026-08-21 22:42

Nothing built after 2026-08-21 had ever been deployed. `build-solution.py` ran its package step
only when the WHOLE solution succeeded, and the solution had been failing for weeks on a missing
`gtest/gtest.h` in two unit-test projects -- 21 errors on the 2026-09-07 build, the very build
believed to have produced the current baseline masters. The DLLs compiled into `build\\bin\\` and
then msbuild returned non-zero, so `main()` called `exit(1)` and the package step never ran.

Four fix IDs were therefore ABSENT from the exporter that produced the masters, while every log
read as a successful build:

    MAX-TEX-004                portable texture paths
    MAX-MTLX-OUTPUTAMT-026     texmap Output amount
    MAX-LIT-AFFECT-025         V-Ray per-channel light gating
    MAX-LIT-HIDDEN-VIS-024     a hidden light must not illuminate

A second, independent cause sat on top of it: the recorded invocation in `C:\\work\\BUILD_CMD.txt`
passes no `-p`/`--package` at all, so even a fully green build staged nothing. That is why the
closing report is part of this fix and not decoration -- silence was the actual failure.

WHAT THIS ASSERTS. The two decision functions are pure, so they are tested directly against
synthetic msbuild output. This runs anywhere; it needs neither Windows nor 3ds Max.
"""
import os
import sys
import unittest

sys.path.insert(
    0,
    os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', 'build-scripts')),
)

import importlib.util

_spec = importlib.util.spec_from_file_location(
    'build_solution',
    os.path.abspath(
        os.path.join(os.path.dirname(__file__), '..', '..', '..', 'build-scripts',
                     'build-solution.py')),
)
build_solution = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(build_solution)


# Real msbuild lines, shape preserved verbatim from the 2026-09-07 build log.
GTEST_FAILURE = [
    r'C:\work\3dsmax-usd\src\Tests\Unit\TestHelpers.h(26,10): error C1083: Cannot open include '
    r"file: 'gtest/gtest.h': No such file or directory "
    r'[C:\work\3dsmax-usd\src\Tests\Unit\USD.Unit.test.vcxproj]',
    r'C:\work\3dsmax-usd\src\UFEUI\Tests\x.cpp(1,1): error C1083: Cannot open include file: '
    r"'gtest/gtest.h': No such file or directory "
    r'[C:\work\3dsmax-usd\src\UFEUI\Tests\UFE.Unit.test.vcxproj]',
    r'  MaxUsd_Translators.vcxproj -> C:\work\3dsmax-usd\build\bin\MaxUsd_Translators.dll',
]

DELIVERABLE_FAILURE = [
    r'C:\work\3dsmax-usd\src\MaxUsd\Utilities\PortableAssetPath.cpp(41,9): error C3861: '
    r"'TfCallContext': identifier not found "
    r'[C:\work\3dsmax-usd\src\MaxUsd\maxUsd.vcxproj]',
]

UNATTRIBUTABLE_FAILURE = [
    'MSBUILD : error MSB1009: Project file does not exist.',
]


class FailedProjectsTest(unittest.TestCase):
    def test_picks_out_the_test_projects(self):
        self.assertEqual(
            build_solution.failed_projects(GTEST_FAILURE),
            {'usd.unit.test.vcxproj', 'ufe.unit.test.vcxproj'},
        )

    def test_a_successful_link_line_is_not_a_failure(self):
        # The Translators link line names no .vcxproj in a diagnostic suffix and carries no
        # 'error', so it must not be mistaken for a failing project.
        self.assertNotIn('maxusd_translators.vcxproj',
                         build_solution.failed_projects(GTEST_FAILURE))

    def test_picks_out_a_deliverable(self):
        self.assertEqual(
            build_solution.failed_projects(DELIVERABLE_FAILURE), {'maxusd.vcxproj'})

    def test_unattributable_error_yields_nothing(self):
        self.assertEqual(build_solution.failed_projects(UNATTRIBUTABLE_FAILURE), set())


class DeployDecisionTest(unittest.TestCase):
    def test_success_packages(self):
        ok, reason = build_solution.deploy_decision(True, set())
        self.assertTrue(ok)
        self.assertIn('succeeded', reason)

    def test_test_only_failure_still_packages(self):
        """The regression this fix exists for."""
        ok, reason = build_solution.deploy_decision(
            False, build_solution.failed_projects(GTEST_FAILURE))
        self.assertTrue(ok)
        self.assertIn('only test projects', reason)

    def test_deliverable_failure_blocks(self):
        ok, reason = build_solution.deploy_decision(
            False, build_solution.failed_projects(DELIVERABLE_FAILURE))
        self.assertFalse(ok)
        self.assertIn('maxusd.vcxproj', reason)

    def test_mixed_failure_blocks(self):
        mixed = build_solution.failed_projects(GTEST_FAILURE + DELIVERABLE_FAILURE)
        ok, _reason = build_solution.deploy_decision(False, mixed)
        self.assertFalse(ok, 'a deliverable failure must block even alongside test failures')

    def test_unattributable_failure_blocks(self):
        """If we cannot tell what broke, we do not ship it."""
        ok, reason = build_solution.deploy_decision(False, set())
        self.assertFalse(ok)
        self.assertIn('no project could be identified', reason)


class ReportTest(unittest.TestCase):
    """The closing report is the part that turns a silent no-op into a visible one."""

    def _capture(self, **kw):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            build_solution.report_deploy_state(**kw)
        return buf.getvalue()

    def test_says_so_when_package_was_never_requested(self):
        out = self._capture(build_ok=True, reason='build succeeded',
                            packaged=False, package_requested=False)
        self.assertIn('PACKAGED: NO', out)
        self.assertIn('--package', out)

    def test_says_so_when_blocked(self):
        out = self._capture(build_ok=False, reason='deliverable projects failed: maxusd.vcxproj',
                            packaged=False, package_requested=True)
        self.assertIn('PACKAGED: NO', out)

    def test_confirms_a_package_still_needs_installing(self):
        out = self._capture(build_ok=True, reason='build succeeded',
                            packaged=True, package_requested=True)
        self.assertIn('PACKAGED: yes', out)
        self.assertIn('Install', out)


if __name__ == '__main__':
    unittest.main(verbosity=2)
