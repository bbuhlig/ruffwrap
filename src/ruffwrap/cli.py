#!/usr/bin/env python3
"""
ruffwrap: A wrapper around the Ruff tool for version pinning and batch processing.

The ruffwrap script is a wrapper around the Ruff tool to allow Ruff configuration to
determine a particular version of Ruff to run, as well as define standard or user-defined
batch modes wrapping multiple Ruff commands used in common operations. The script
mines Ruff configuration for sentinel tokens to define the appropriate Ruff version
as well as activate standard batch mode definitions or specify user-defined ones.

Generally there are two types of operation. The first type is single mode and is
selected through the absence of a --mode argument.
This mode processes the __RUFFWRAP_EXEC__ sentinel token from Ruff configuration to determine
the version of Ruff to execute, then executes that version with the provided passthrough arguments.

The second type is a batch mode and is selected by passing a --mode=MODENAME argument,
where MODENAME represents an arbitrary sequence of Ruff commands that should be run against
whatever files are passed on the command line. The definition of MODENAME occurs by either
activating a standard batch mode definition provided by the sentinel
__RUFFWRAP_MODE_modename_STANDARD_DEFINITION__ for the below modenames; or else one or more
__RUFFWRAP_MODE_modename_CMD_##__ sentinels defining a user-defined sequence of arbitary Ruff
commands that should be executed in a particular order.
In either case, the batch mode command executes until any command in the sequence fails. If
modename is not defined through configuration file sentinels, the mode command exits immediately
with normal status. As with Single mode, the __RUFFWRAP_EXEC sentinel token is also processed
to determine the Ruff version to execute. Standard definitions for the following modes can be
activated by specifying the associated __RUFFWRAP_MODE_modename_STANDARD_DEFINITION__ sentinel.

- hook: This mode is often leveraged by git pre-commit hook and developer run-on-save actions,
  to run the Ruff formatter and ruff check --no-fix actions on the saved file or changed
  files in the commit.
- hook-fix: This mode runs the Ruff formatter and ruff check --fix actions to also
  automatically fix various linter problems, and can be leveraged by a git pre-commit hook
  or a custom developer IDE task. It is strongly discouraged for use as a developer
  Ruff-on-save hook.
- verify: This mode is leveraged by CI, and is intended to confirm neither the linter nor
  the formatter find any unexpected problems with the files that are changing.
- enroll: This mode is used to initially enroll a legacy codebase, or to re-enroll a
  codebase after a Ruff upgrade or configuration change generates differences in linting
  or formatting.

Usage:
    ruffwrap [options] [passthrough_args]
    ruff [--ruffwrap-options] [passthrough_args]

Options (prefix each word with "ruffwrap-" when invoking as ruff instead of ruffwrap):
    --mode=<mode>  Specify the mode to run in, e.g. hook, hook-fix, verify, enroll, ...
    --mode-require Fail the command if the specified mode is not defined
    --verbose      Increase the verbosity of the output
    --version      Show the version of ruffwrap and exit
    --help         Show this help message and exit

Passthrough arguments:
    In Single mode, all additional arguments are passed through to the Ruff tool.
    In a Batch mode, additional arguments are filepaths passed to the Ruff tool for each
    batch command. As a protection against misspelled double-hyphen arguments being treated
    as paths, the argument list can begin with a "--" argument to explicitly note the start
    of the pathlist. Any passthrough arguments found before the "--" argument will
    be treated as an error.

Environment variables:
    RUFFWRAP_EXEC: Specify a default path to the Ruff tool executable, which will be used
                   to parse the Ruff configuration. Any __RUFFWRAP_EXEC__ sentinels in
                   discovered Ruff configuration will override this default for ensuing
                   Ruff commands. The default is /usr/bin/ruff.
    RUFFWRAP_SKIP: If set, skip sentinel token processing, causing Single mode to be activated,
                   calling RUFFWRAP_EXEC against all passthrough args.

"""

import argparse
import os
import re
import shlex
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING, Self

if TYPE_CHECKING:
    from collections.abc import Callable

VERSION = None  # This will be updated by the build/deployment generation tool


class ModeBase:
    """
    Base class for different modes of operation.

    This class serves as a base for different modes of operation. It sets up the
    initial state of the mode, including the sentinels map, skip flag, and
    current working directory.
    """

    def __init__(self: Self, args: argparse.Namespace) -> None:
        """
        Initialize the ModeBase class.

        This class serves as a base for different modes of operation. It sets up the
        initial state of the mode, including the sentinels map, skip flag, and
        current working directory.

        Args:
            args (argparse.Namespace): The parsed command-line arguments.
        """
        self._reset()
        self._sentinels_map: dict[str, Callable[[re.Match | None], str]] = {
            self._sentinel_exec(): self._sentinel_exec,
        }
        self._skip = os.environ.get("RUFFWRAP_SKIP")
        self._args = args
        self._initwd = os.getcwd()
        self._cwd_rel_str = ""

    def _reset(self: Self) -> None:
        self._exec = ""
        self._extraargs = {}

    def _sentinel_exec(self: Self, match: re.Match | None = None) -> str:
        if not match:
            return r"__RUFFWRAP_EXEC__(?P<EXEC>(.+)),$"

        self._exec = match.group("EXEC")
        return r""

    def ruff(self: Self, *cmdargs: str, verbosity_threshold: int = 1) -> list[str]:
        """
        Construct a command array for the Ruff tool.

        This method constructs a command array for the Ruff tool, taking into account
        the current working directory, verbosity threshold, and any additional command
        arguments. The command array is constructed from the base command, which is
        determined by the RUFFWRAP_EXEC environment variable or the default command
        if RUFFWRAP_SKIP is not set.

        Args:
            *cmdargs: Variable number of command arguments to be passed to the Ruff tool.
            verbosity_threshold: The minimum verbosity level required to print the command.

        Returns:
            A list of strings representing the command array.
        """
        exec = os.environ.get("RUFFWRAP_EXEC", None) if self._skip else (self._exec or None)
        if exec is None:
            exec = shutil.which("ruff")
            if exec is None:
                exec = shutil.which("uvx")
                if exec:
                    exec = exec + " ruff"

        if exec is None:
            raise FileNotFoundError(f"ruff, uvx not found on PATH")
        exec = shlex.split(exec)

        cmd_ary = [*exec, *cmdargs]

        if self._args.verbose >= verbosity_threshold:
            print(
                f"<<< {self._cwd_rel_str}{subprocess.list2cmdline(cmd_ary)} >>>", file=sys.stderr
            )  # python<3.8 has no shlex.join
        return cmd_ary

    def process_sentinels(self: Self) -> None:
        """
        Process sentinel tokens from the Ruff tool output.

        This method runs the Ruff tool with specific options to retrieve the list of
        sentinel tokens. It then parses the output to extract the sentinel tokens and
        calls the corresponding functions to process them.

        The method first checks if there are any files to process. If not, it returns
        immediately. Otherwise, it iterates over the output lines, looking for the
        sentinel token header. Once found, it processes each line as a potential
        sentinel token, calling the corresponding function if a match is found.

        Args:
            None

        Returns:
            None
        """
        looking_for_sentinel_header = True

        try:
            result = subprocess.run(
                self.ruff(
                    "check",
                    "--show-settings",
                    "--config",
                    "include = [ '*', '.*' ]",
                    "--config",
                    "exclude = [ '*/*' ]",
                    "--config",
                    "cache-dir = '/dev/null'",
                    "--no-cache",
                    verbosity_threshold=2,
                ),
                text=True,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError as e:
            if "No files found under the given path" in e.stderr:
                # No files to process; don't worry about sentinels
                return
            print(e.stderr, file=sys.stderr)
            raise

        for line in result.stdout.splitlines():
            if looking_for_sentinel_header:
                if line.startswith("linter.builtins = ["):
                    looking_for_sentinel_header = False
                else:
                    continue

            if line[-1] == "]":
                # found end of sentinel token; we're done.
                return

            # This line may be a sentinel token; try to match
            for pattern, func in self._sentinels_map.items():
                match = re.search(pattern, line)
                if match:
                    func(match)
                    break


class SingleMode(ModeBase):
    """
    SingleMode mode of the Ruff tool.

    This class extends the :class:`ModeBase` class and provides the Single mode of operation.
    It processes sentinel tokens from the Ruff tool output if RUFFWRAP_SKIP is not set,
    and then executes the Ruff tool with the provided passthrough arguments.
    """

    def run(self: Self, passthrough_args: list[str]) -> int:
        """
        Run the Single mode of the ruffwrap tool.

        This method processes sentinel tokens from the Ruff tool output if RUFFWRAP_SKIP is not set.
        It then executes the Ruff tool with the provided passthrough arguments.

        Args:
            passthrough_args: A list of command arguments to be passed to the Ruff tool.

        Returns:
            An integer representing the exit status of the Ruff tool execution.
        """
        if not self._skip:
            self.process_sentinels()
        try:
            execargs = self.ruff(*passthrough_args)
            os.execvp(execargs[0], execargs)
        except OSError as e:
            msg = f"Error executing {execargs}: {e}"
            print(msg, file=sys.stderr)
            return 200
        # unreachable due to successful os.execvp


class BatchMode(ModeBase):
    """
    Batch mode of the Ruff tool.

    This class extends the :class:`ModeBase` class and provides the batch mode of operation.
    It processes sentinel tokens from the Ruff tool output if RUFFWRAP_SKIP is not set,
    and then executes the Ruff tool for each batch mode specific command on the provided paths.
    """

    def __init__(self: Self, args: argparse.Namespace) -> None:
        """
        Initialize the BatchMode class.

        This method initializes the BatchMode class by calling the parent class's
        constructor and updating the sentinels map with additional sentinel tokens.

        Args:
            args (argparse.Namespace): The parsed command-line arguments.

        Returns:
            None
        """
        super().__init__(args)
        self._sentinels_map.update(
            {
                self._sentinel_default_definition(): self._sentinel_default_definition,
                self._sentinel_cmd(): self._sentinel_cmd,
            }
        )

    def _reset(self: Self) -> None:
        super()._reset()
        self._modes = {}
        self._mode_default_definition_funcs = {
            "hook": BatchMode._get_hook_mode_default_definition,
            "hook-fix": BatchMode._get_hook_fix_mode_default_definition,
            "verify": BatchMode._get_verify_mode_default_definition,
            "enroll": BatchMode._get_enroll_mode_default_definition,
        }

    def _sentinel_cmd(self: Self, match: re.Match | None = None) -> str:
        if not match:
            return r"__RUFFWRAP_MODE_(?P<MODE>([a-zA-Z0-9\-\_]+))_CMD_(?P<IDX>([\d+]))__(?P<ARGS>(.*)),$"
        mode = match.group("MODE")
        idx = int(match.group("IDX"))
        args = shlex.split(match.group("ARGS"))
        if mode not in self._modes:
            self._modes[mode] = {}
        self._modes[mode][idx] = args
        return r""

    def _sentinel_default_definition(self: Self, match: re.Match | None = None) -> str:
        if not match:
            return r"__RUFFWRAP_MODE_(?P<MODE>([a-zA-Z0-9\-\_]+))_DEFAULT_DEFINITION__,$"
        mode = match.group("MODE")
        def_func = self._mode_default_definition_funcs.get(mode, None)
        self._modes[mode] = def_func() if def_func else {}
        return r""

    @staticmethod
    def _get_hook_mode_default_definition(*, fix_arg: str = "--no-fix") -> dict[int, list[str]]:
        """Get the default definition for "hook" mode.

        Hook mode is leveraged by git pre-commit hook and developer run-on-save
        actions. Typical Usage:
        ruffwrap --verbose --mode=hook <files_changing> 2>&1.
        """
        default_definition = [
            # Start by running the linter on existing code.
            # The "fix_arg" in the standard version is empty, but see the hook-fix
            # mode definition below on a version that sets this to " --fix" so that
            # "ruff check --fix" is used.
            f"check {fix_arg}",
            # ...then run the formatter.
            #
            # If you want to run either the formatter or the linter or not both, instead of
            # using the defaults just define the mode directly using one of these two
            # first commands and nothing else.
            "format",
            # The Ruff formatter is not aware of the lint configuration therefore could
            # induce lint problems, and vice versa. For example, to satisfy its best-
            # effort line length constraint, the formatter could decide to break up a one-line
            # function call with a long list of arguments into multiple lines, one line per
            # argument. noqa's may have been suffixed to the end of the one-line function call
            # related to the argument list and no longer belong there versus at the end of the
            # line where the associated argument has moved to. But the formatter doesn't know
            # about or understand the noqas, and just leaves them in the wrong place. Therefore
            # to satisfy the linter, some of the noqa comments will need to be moved. We don't
            # easily know which ones or to where, but the move of some can be decomposed into
            # deleting all of them then re-adding all of them with the same net result.
            # This command and the next accomplishes the move in separate steps.
            #
            # This 1st of the 2 commands removes all noqas, and is tricky in how it does so.
            # Specifically it tells ruff to disable all lint rules except RUF100 (unused-noqa),
            # then to auto-fix those unused-noqa's which ruff satisfies by deleting them.
            # But by having ALL other lint rules off, all noqas become unused. Therefore the
            # auto-fix deletes ALL noqas.
            #
            # An aside, it is not expected that a failure of this or any remaining commands
            # should constitute a problem needing the user's attention; i.e. a pre-commit
            # hook should always return 0 at this point even if the formatter check fails.
            # Add --quiet switch to most all remaining commands to make them less
            # chatty and confusing to a user who isn't as aware of all the context.
            # They will still report problems.
            "check --fix-only --select RUF100 --quiet",
            # This 2nd of the 2 commands to move re-formatted noqa's will
            #  add them all back in again.
            #
            # At this point, there will be zero noqas unused (RUF100) or deprecated (RUF101)
            # even though we are not enabling those linter rules specifically, nor could we without
            # undesirably introducing noqas for them in some cases due to the formatter.
            # As far as normal (non-noqa) comments, the formatter will leave them at the same
            # location relative to whatever normal syntax token they were closest to (like
            # a colon following an if statement), but not with regard to other comments. This
            # means that any comments suffixed after a noqa to describe the reason for
            # the noqa will not move to the noqa's new location. If a noqa does not move, the
            # deletion of all noqas and readdition will move any comments that were at the right
            # of the noqa to the left of the noqa. Notably, the formatter ignores line length
            # restrictions for comments next to noqa lines, so there will be no line length
            # rule problems as a result so long as the E501 (line-too-long) linter rule is
            # disabled as per documented ruff formatter recommendations.
            "check --add-noqa --quiet",
            # 99.9% of the time, at this point the CI check on formatting and lint should pass.
            # But in edge cases the last --add-noqa will trigger another format violation.
            # Therefore, re-run the formatter.
            "format --quiet",
            # And then this could trigger lint violations, so re-apply all noqa's by stripping...
            "check --fix-only --select RUF100 --quiet",
            # Then readding
            "check --add-noqa --quiet",
            # And then here there are even rarer edge cases (ruff bugs?) where another pass
            # or two or ?infinity of both formatter and linter could be required before checks
            # run by CI will simultaneously pass. Workaround the instability by having the
            # pre-commit hook parse the output of these checks. If the instability marker is
            # found, it needs to embed a flag in the commit message to tell CI to skip its
            # checking on this commit. The flag needs to be parsed by CI only as applicable
            # for the commit's root tree SHA in order to skip instances where commit is
            # cherry-picked or rebased in some way where the process it executed shouldn't
            # be applied toward deciding to skip CI.
            "check --exit-zero --no-fix --output-format=json-lines",
            # Linter can --exit-zero even if issues are found whereas the formatter can't,
            # so run the linter check first. Reminder that the pre-commit hook should always
            # return 0 at this point even if the formatter check fails.
            "format --quiet --check",
        ]

        return {idx: shlex.split(cmd_str) for idx, cmd_str in enumerate(default_definition)}

    @staticmethod
    def _get_hook_fix_mode_default_definition() -> dict[int, list[str]]:
        """Get the default definition for "hook-fix" mode.

        Hook-fix mode can be leveraged by git pre-commit hook actions. Strongly
        discouraged for use as a developer ruff-on-save hook, see below comments
        Typical Usage:
        ruffwrap --verbose --mode=hook-fix <files_changing> 2>&1
        """
        # The idea of an initial "ruff check --fix" is a nice developer perk for auto-fixing
        # issues on explicit developer action e.g. git commit when run by a pre-commit hook.
        # But when used as a developer ruff-on-save action, it can cause loss of work.
        # For example, the autofix for F841 (unused-variable) is to remove the variable
        # assignment, so if run as part of a ruff-on-save hook and the developer saves
        # between writing the line assigning the variable but before using the variable,
        # or typoes either the variable assignment or reference, the autofix will immediately
        # and silently remove the assignment, at best irritating and perhaps bewildering
        # the developer. Therefore leveraging --hook-fix as a developer ruff-on-save hook
        # is strongly discouraged.
        return BatchMode._get_hook_mode_default_definition(fix_arg="--fix")

    @staticmethod
    def _get_verify_mode_default_definition() -> dict[int, list[str]]:
        """Get the default definition for "verify" mode.

        Verify mode is leveraged by CI, and is intended to confirm neither the linter
        nor the formatter find any unexcepted problems with the files that are changing.
        Typical Usage:
        set -xe
        if [[ -z "$skip_ruffwrap_check" ]]; then
            set -o pipefail
            git diff --cached --name-only -z | xargs -0 bash -c 'ruffwrap --verbose --mode=verify "$@" 2>&1'
        else
            echo "skip_ruffwrap_check reason: $skip_ruffwrap_check"
        fi
        """
        default_definition = [
            "check --no-fix --no-cache --config \"cache-dir = '/dev/null'\"",
            "format --check --no-cache --config \"cache-dir = '/dev/null'\"",
        ]
        return {idx: shlex.split(cmd_str) for idx, cmd_str in enumerate(default_definition)}

    @staticmethod
    def _get_enroll_mode_default_definition() -> dict[int, list[str]]:
        """Get the default definition for "verify" mode.

        Enroll mode is used to initially enroll a legacy codebase, or to re-enroll a codebase
        after a Ruff upgrade generates differences in linting or formatting.
        Typical Usage (from submodule root, after updating RUFFWRAP_EXEC to new version in Ruff configuration):
        - git ls-files -z --recurse-submodules | xargs -0 bash -c 'ruff --ruffwrap-verbose --ruffwrap-mode=enroll "$@" 2>&1'
        - git add -u && git commit
        - git rev-parse HEAD >> .git-blame-ignore-revs; git add .git-blame-ignore-revs && git commit
        - git push origin HEAD~1:refs/for/<branch> && git commit origin HEAD:refs/for/<branch>
        - verify and submit
        - Repeat for all active branches (i.e. with potential merge/cherry-pick needs)
        """
        # While unlikely on modern day linux, it's possible with the git ls-files command to exceed the maximum
        # command line argument length. A command to verify would be to run the below command and confirm it does
        # not approach the value returned by "getconf ARG_MAX" (typically 2097152 characters)
        # git ls-files -z --recurse-submodules | wc -c

        default_definition = [
            # In the hook modes, it's better to run the check first, before the format, to
            # get lint failure feedback to the user before messing with the file format.
            # But with enrollment which is expected to do whatever is necessary to get the
            # linter and formatter not complaining, it's more efficient to format first
            # before involving the linter.
            "format",
            # --add-noqa in the next step would ordinarily add a noqa for
            # PLR2044 (empty-comment) for any empty comments at the end of python
            # statements. But it was observed that with that noqa, Ruff no longer
            # sees the line as having a empty comment, removing the need for it
            # and causing it to be stripped in later steps, which reinduces the
            # empty-comment problem seen by the linter. Therefore explicitly fix this
            # particular issue instead of exempting it with noqa. This seems like
            # a workaround to a Ruff bug, should be checked if still necessary in
            # later Ruff releases.
            "check --fix-only --select PLR2044 --quiet",
            # Add noqas for all known configured issues per the current version of Ruff
            "check --add-noqa",
            # Doing this may have induced format problems, so re-run the formatter.
            # Add --quiet switch to all remaining commands to make them less
            # chatty. They will still report problems.
            "format --quiet",
            # Formatting may induced, solved, or moved linter problems, so strip
            # all noqas via the RUF100 autofix trick...
            "check --fix-only --select RUF100 --quiet",
            # then readd them all again.
            "check --add-noqa --quiet",
            # Doing this may have (again) induced format problems, so re-run the formatter
            "format --quiet",
            # Very unlikely, but formatting may induced some more issues. Just try to add
            # more noqas.
            "check --add-noqa --quiet",
            # More noqas could trigger another re-format need
            "format --quiet",
            # Hopefully that didn't trigger any linter failures.  If this fails, re-run
            # this mode a few times to hopefully fix. If that doesn't work, there is
            # probably a ruff bug involved and it will likely be required to fix or
            # some linter issues or perhaps manually reformat to enroll.
            "check --no-fix --quiet",
        ]
        return {idx: shlex.split(cmd_str) for idx, cmd_str in enumerate(default_definition)}

    def _get_files_by_depth(self: Self, paths: list[str]) -> dict[int, dict[str, set[str]]]:
        files_by_depth = {}
        for path in paths:
            if os.path.isdir(path):
                continue
            abspath = os.path.abspath(path)
            file_dir = os.path.relpath(os.path.dirname(abspath), self._initwd)
            depth = file_dir.count("/")
            if depth not in files_by_depth:
                files_by_depth[depth] = {}
            if file_dir not in files_by_depth[depth]:
                files_by_depth[depth][file_dir] = set()
            files_by_depth[depth][file_dir].add(abspath)
        return files_by_depth

    def _get_paths_from_args(self, args: list[str]) -> tuple[bool, list[str]]:
        """Return path list from argument list."""
        try:
            if (filelist_delim := args.index("--")) > 0:
                return False, args[0:filelist_delim]
            paths = args[(1 + filelist_delim) :]
        except ValueError:
            # "--" not in the arglist; assume they are all paths.
            paths = args
        return True, paths

    def run(self: Self, args: list[str]) -> int:
        """
        Run the batch mode of the ruff tool.

        This method processes sentinel tokens from the ruff tool output if RUFFWRAP_SKIP is not set.
        It then executes the ruff tool with the provided paths and mode-specific commands.

        Args:
            args: Generally a list of file paths to be processed by the ruff tool.
                  If a "--" is passed, any prior args are flagged as an error.
        Returns:
            An integer representing the exit status of the ruff tool execution.
        """
        if self._skip:
            return 0

        paths_are_good, paths = self._get_paths_from_args(args)
        if not paths_are_good:
            print(f"bad {self._args.mode} mode args, failing (exit code 3): {paths}", file=sys.stderr)
            return 3

        returncode = 0
        # iterate over files in reverse order of depth
        for _, dir_info in sorted(self._get_files_by_depth(paths).items(), reverse=True):
            for dir_path, abs_paths in dir_info.items():
                abs_dir_path = f"{self._initwd}/{dir_path}"
                os.chdir(abs_dir_path)
                rel_dir = os.path.relpath(abs_dir_path, self._initwd)
                self._cwd_rel_str = f"{rel_dir} $ " if rel_dir != "." else ""
                self._reset()
                self.process_sentinels()

                if self._args.mode not in self._modes:
                    if self._args.mode_require:
                        print(
                            f'{abs_dir_path}: mode "{self._args.mode}" undefined; mode-require set, failing (exit code 1)',
                            file=sys.stderr,
                        )
                        returncode = max(1, returncode)
                    continue

                # Get the files that need to be checked from ruff
                paths = [
                    os.path.basename(file)
                    for file in subprocess.check_output(self.ruff("check", "--show-files", verbosity_threshold=2))
                    .decode("utf-8")
                    .splitlines()
                    if file in abs_paths
                ]

                if not paths:
                    continue

                mode = self._modes[self._args.mode]

                try:
                    for _, cmd in sorted(mode.items()):
                        subprocess.check_call(self.ruff(*cmd, *paths))
                except subprocess.CalledProcessError as e:
                    print(e, file=sys.stderr)
                    returncode = max(e.returncode, returncode)
                    continue

        return returncode


def main() -> int:
    """
    Execute the main entry point for the ruffwrap script.

    This script provides a wrapper around the Ruff tool, allowing Ruff
    configuration to specify a version to execute as well as optional sets of batch
    commands to execute.

    Args:
        None

    Returns:
        An integer representing the exit status of the ruff tool execution.
    """
    versuffix = "" if VERSION is None else f"\n\nVersion: {VERSION}"
    parser = argparse.ArgumentParser(
        add_help=False,
        description=__doc__ + versuffix,  # type: ignore[reportOptionalOperand]
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    invoked_as = os.path.basename(os.environ.get("RUFFWRAP_INVOKED_AS",sys.argv[0]))
    arg_prefix = "ruffwrap-" if invoked_as != "ruffwrap" else ""
    parser.add_argument(f"--{arg_prefix}help", action="help")
    parser.add_argument(f"--{arg_prefix}mode", type=str, dest="mode")
    parser.add_argument(f"--{arg_prefix}mode-require", action="store_true", dest="mode_require")
    parser.add_argument(f"--{arg_prefix}verbose", action="count", default=0, dest="verbose")
    parser.add_argument(f"--{arg_prefix}version", action="store_true", dest="version")
    ruffwrap_args, passthrough_args = parser.parse_known_args()
    if ruffwrap_args.version:
        if VERSION is None:
            print("Unknown", file=sys.stdout)
        else:
            print(VERSION, file=sys.stdout)
        return 0
    if ruffwrap_args.mode:
        rc = BatchMode(ruffwrap_args).run(args=passthrough_args)
    else:
        rc = SingleMode(ruffwrap_args).run(passthrough_args)
    return rc


if __name__ == "__main__":
    sys.exit(main())
