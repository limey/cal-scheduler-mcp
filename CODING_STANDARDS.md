# Coding standards

The checkable rules for code, comments, docstrings, and tests in this
repository. Single source of truth for writing conventions; treat any
disagreement between this file and prose elsewhere as this file winning.

## Voice

Use the fewest words that carry the meaning. Avoid filler ("It is worth
noting...", "Note that...", "In order to..."). Prefer declarative present
tense. Use lists for parallel structure, prose for argument.

## Cross-references

Do not reference other local files from code, comments, or docstrings.
Do not reference non-durable external identifiers (eval numbers, issue
numbers, PR numbers). The source must make sense to a reader who has
never seen the rest of the project.

## Speculation

Document what the code does *now*. No "if a future PR adds X, this could
be relaxed." Future-state reasoning belongs in the issue tracker.

## Docstrings

1 line default. 2 lines max. If a function needs more, split the function
or rename it. The docstring describes the contract, not the implementation.

## Comments

Prefer code that does not need a comment. A good name beats an explanatory
comment. Comments explain *why*, not *what*. Section-header comments in
test files: 1 line, only when grouping is genuinely needed.

## Tests

Test names carry the assertion. No test docstrings. If a test's *why* is
non-obvious, the why belongs in the commit message. Test bodies stay short;
factor setup into a named helper.

## Commit messages

The *why* lives in the commit message, not the source. Long commit
messages are welcome; long docstrings are not. One concern per commit;
single squash commit per feature.
