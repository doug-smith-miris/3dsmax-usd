#
# Copyright 2024 Autodesk
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#
import os
import re
import subprocess
import argparse

# map to handle the dependencies location for the project
#  the key is the script parameter name
#  the value is a pair
#    1) argument to pass to msbuild (the referenced property in vcxproj/props)
#    2) the help text to display when help is invoked for the current script
artifact_map = {'maxsdk':('MaxSDK', 'The path location for the \'MaxSDK\' folder.'),
                'qtinstall':('QtInstall', 'The Qt reference version from QtVsTools (aka \'Qt Installation\').'),
                'pybind11inc':('PyBind11Inc', 'The path location for the \'pybind11\' include folder.'),
                'materialx':('MaterialXDir', 'The path location for the 3ds Max MaterialX material plugin folder.'),
                'googletest':('GoogleTestDir', 'The path location for the \'gtest\' folder.'),
                'pyopengl':('PyOpenGLDir', 'The path location for the \'OpenGL\' Python module (PyOpenGL).'),
                'maxusddevkit':('MaxUsdDevKit', 'The path location for the 3ds Max USD \'devkit\'.'),
                'spdlog':('SpdlogInc', 'The path location for \'spdlog\' include folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'python':('PythonLocation', 'The path location for the \'Python\' folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'pyside':('PySideDir', 'The path location for the \'PySide6\' Python module. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'shiboken':('PySideShibokenDir', 'The path location for the \'shiboken6\' Python module. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'ufeinc':('UfeInc', 'The path location for the \'Ufe\' include folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'ufelib':('UfeLib', ' The path location for the \'Ufe\' lib folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'usdufe':('UsdUfeDir', 'The path location for the \'UsdUfe\' folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'usdlayereditor':('UsdLayerEditorDir', 'The path location for the \'UsdLayerEditor\' folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'usdsharedcomponent':('UsdSharedComponentsDir', 'The path location for the \'usdSharedComponents\' folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'assetresolver':('AdskAssetResolverDir', 'The path location for the \'adskassetresolver\' folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'openusd':('PxrUsdRoot', 'The path location for the \'OpenUSD\' folder. If not provided, using the path from the \'devkit\' if the \'maxusddevkit\' option is provided.'),
                'tbb':('TBBDir', 'The path location for the \'TBB\' folder. If not provided, using the path from the \'OpenUSD\' if the \'openusd\' or \'maxusddevkit\' option is provided.'),
                'boostinc':('BoostInc', 'The path location for the \'Boost\' include folder. If not provided, using the path from the \'OpenUSD\' if the \'openusd\' or \'maxusddevkit\' option is provided.'),
                'boostlib':('BoostLib', ' The path location for the \'Boost\' lib folder. If not provided, using the path from the \'OpenUSD\' if the \'openusd\' or \'maxusddevkit\' option is provided.')}

def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("configuration", 
                        nargs='?',
                        type=str.lower,
                        choices=['release', 'hybrid'],
                        default='release',
                        help="The build configuration type.")
    parser.add_argument("target", choices=[2024, 2025, 2026, 2027], help="The 3ds Max version to target.", type=int)
    parser.add_argument("-b", "--build", help="The build number coming from the pipeline.", default=0, type=int)
    parser.add_argument("-v", "--version", help="The 3ds Max USD component version being built.", default='0.0.0')
    parser.add_argument("-w", "--warnaserror", help="Enable the compiler to treat all warnings as errors.", action='store_true')
    parser.add_argument("-r", "--rebuild", help="Rebuild the project.", action='store_true')
    parser.add_argument("-d", "--distrib", help="Prepare for redistribution. Write component version in source headers.", action='store_true')
    parser.add_argument("-p", "--package", help="Prepare the package folder after build.", action='store_true')
    # parse arguments to set the artifact specific paths if any
    for arg, data in artifact_map.items() :
        parser.add_argument(f"--{arg}", help=data[1])
    return parser.parse_args()

def get_script_folder() -> str :
     return os.path.dirname(os.path.abspath(__file__))

def build_command(args:argparse.Namespace) -> list:
    # path to build-scripts
    swd = get_script_folder()
    # set up the build environment
    cmd = [swd + "\\configure-vsdevcmd.bat"]
    if args.target >= 2026:
        cmd.append("2022")
    else:
        cmd.append("2019")

    # append the build command
    cmd.append("&&")

    cmd.append('msbuild.exe')
    cmd.append(swd + "\\..\\src\\usd-component.sln")

    if args.rebuild:
        cmd.append('/t:rebuild')
    if args.warnaserror:
        cmd.append('/warnaserror')
    cmd.append(f'/p:Configuration={args.configuration}')
    cmd.append(f'/p:Platform=x64')
    if args.distrib:
        cmd.append(f'/p:BuildType=jenkins')
    cmd.append(f'/p:VersionTarget={args.target}')
    cmd.append(f'/p:BuildNumber={args.build}')
    cmd.append(f'/p:ComponentVersion={args.version}')

    # parse the artifacts variable option list 
    args_dict = vars(args)
    for arg, data in artifact_map.items() :
        if args_dict[arg] is not None:
            cmd.append(f'/p:{data[0]}={args_dict[arg]}')

    return cmd

# MAX-BUILD-DEPLOY-028 -----------------------------------------------------------------------
# Projects that ship nothing. A failure confined to these must not stop the plugin being packaged.
#
# The two unit-test projects have been failing on a missing `gtest/gtest.h` for weeks. Because
# `main()` exited on ANY non-zero msbuild result, the package step never ran, and the DLLs sat
# unused in `build\bin\` while 3ds Max kept loading `maxUsd.dll` from 2026-08-21. Four fix IDs
# (MAX-TEX-004, MAX-MTLX-OUTPUTAMT-026, MAX-LIT-AFFECT-025, MAX-LIT-HIDDEN-VIS-024) were absent
# from the exporter that produced the baseline masters, while every log said the build succeeded.
TEST_ONLY_PROJECTS = {
    'usd.unit.test.vcxproj',
    'usd.system.test.vcxproj',
    'ufe.unit.test.vcxproj',
}

# msbuild appends `[<full path>.vcxproj]` to every diagnostic. That suffix is the only place it
# says WHICH project a failure belongs to.
_PROJECT_IN_DIAGNOSTIC = re.compile(r'\[([^\]\r\n]*\.vcxproj)\]\s*$')


def failed_projects(output_lines):
    """Basenames of the .vcxproj files named on error lines, lowercased."""
    failed = set()
    for line in output_lines:
        low = line.lower()
        if ' error ' not in low and not low.lstrip().startswith('error'):
            continue
        match = _PROJECT_IN_DIAGNOSTIC.search(line.rstrip())
        if match:
            # Split on BOTH separators rather than os.path.basename: msbuild emits Windows paths,
            # and this logic is unit-tested on macOS where os.path would not split them at all.
            leaf = re.split(r'[\\/]', match.group(1).strip())[-1]
            failed.add(leaf.lower())
    return failed


def deploy_decision(build_ok, failed):
    """(should_package, reason).

    A deliverable failure blocks packaging; a failure confined to the test projects does not.
    An unattributable failure also blocks -- if we cannot tell what broke, we do not ship it.
    """
    if build_ok:
        return True, 'build succeeded'
    if not failed:
        return False, 'build failed and no project could be identified from the output'
    blocking = sorted(p for p in failed if p not in TEST_ONLY_PROJECTS)
    if blocking:
        return False, 'deliverable projects failed: ' + ', '.join(blocking)
    return True, 'only test projects failed (' + ', '.join(sorted(failed)) + ')'


def report_deploy_state(build_ok, reason, packaged, package_requested):
    """Say what happened to the artefacts, always, in a form nobody can skim past.

    The original failure mode was silence: a build that logged success, packaged nothing, and left
    Max loading a three-week-old DLL. An unmissable closing statement is the actual fix.
    """
    bar = '=' * 78
    lines = [bar]
    lines.append('  BUILD %s -- %s' % ('OK' if build_ok else 'FAILED', reason))
    if packaged:
        lines.append('  PACKAGED: yes. Install it into 3ds Max or the plugin stays unchanged.')
    elif not package_requested:
        lines.append('  PACKAGED: NO -- --package/-p was not passed, so nothing was staged.')
        lines.append('  The compiled DLLs are in build\\bin\\ and 3ds Max will keep loading')
        lines.append('  whatever is already installed. Re-run with --package to stage them.')
    else:
        lines.append('  PACKAGED: NO -- blocked by the build result above.')
    lines.append(bar)
    print('\n'.join(lines))


def build_component(args:argparse.Namespace):
    cmd = build_command(args)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    print(f"Launching build process\n{proc.args}")
    captured = []
    for raw_line in proc.stdout:
        print(raw_line, end='')
        captured.append(raw_line)
    proc.wait()
    # The output is kept so the caller can tell WHICH projects failed. Returning only a boolean
    # is what made a test-only failure indistinguishable from a broken plugin.
    return proc.returncode == 0, captured

def package(config:str, target:int, version:str, build:int):
    # path to build-scripts
    swd = get_script_folder()
    # set up the build environment
    cmd = ['powershell',
           '-ExecutionPolicy',
           'ByPass',
           '-File',
           swd + "\\Prepare-BinPackage.ps1",
           '-SourceFolder',
           f'{swd}\\..\\build\\bin\\x64\\{config}\\usd-component-{target}',
           '-DestinationFolder',
           f'{swd}\\..\\package\\3dsmax-usd-{target}',
           '-ComponentVersion',
           version,
           '-TargetVersion',
           str(target),
           '-BuildNumber',
           str(build)]
    
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
    print(f"Launching packaging process\n{proc.args}")
    for raw_line in proc.stdout:
        print(raw_line, end='')
    proc.wait()
    return proc.returncode == 0
    
def main():
    args = parse_arguments()
    build_ok, output_lines = build_component(args)
    should_package, reason = deploy_decision(build_ok, failed_projects(output_lines))

    packaged = False
    if should_package and args.package:
        packaged = package(args.configuration, args.target, args.version, args.build)

    report_deploy_state(build_ok, reason, packaged, args.package)

    # A deliverable failure, or a packaging step that was asked for and did not succeed, is a
    # failed run. A test-only failure is not: the plugin is good and must be allowed to ship.
    if not should_package:
        exit(1)
    if args.package and not packaged:
        exit(1)
    if not build_ok:
        exit(0)

if __name__ == "__main__":
    main()