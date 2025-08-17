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

