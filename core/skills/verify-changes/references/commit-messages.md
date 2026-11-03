# Commit message checks

Read this only when commit messages are proposed or the request that invoked this skill asks for `commit`. The convention is defined once in the `git-workflow` rule; validate each proposed message against it rather than against a restated copy.

Beyond the format, check each message against the actual diff it would commit: the type describes what the diff does, the scope (if any) matches the touched area, the commit holds one logical change, and breaking changes are marked.

Report each message as OK or with the concrete error and suggested correction. Validating messages does not by itself authorize committing.
