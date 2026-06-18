# Contributing

Thanks for your interest in `cal-scheduler`. Bug reports, documentation
improvements, feature ideas, and code changes are all welcome.

## Reporting bugs and feature requests

Open a GitHub issue. A bug report needs a summary, repro steps, expected
vs actual behavior, and the environment. A feature request should
describe the problem first, then the proposed shape and the alternatives
considered. The bug and feature issue templates prompt for these.

## Submitting changes

1. Fork the repository and clone your fork.
2. Create a branch: `git checkout -b feat/my-change`.
3. Make your change. One concern per branch; small, focused commits;
   the PR is squash-merged.
4. Run the checks locally — they are the same checks CI runs.
5. Open a pull request against `main`. The PR description guides a
   reader through the diff; the diff itself is the source of truth.

## Development setup

The project is `uv`-driven. Python 3.11 or newer.

```bash
git clone https://github.com/limey/cal-scheduler-mcp
cd cal-scheduler-mcp
uv sync
```

`uv sync` resolves `uv.lock` and installs the dev tools (`pytest`,
`ruff`) into the project's `.venv`.

## Before you push

```bash
uv run ruff check   # lint
uv run pytest -q    # unit tests; no CalDAV server required
```

## Design and writing

`PHILOSOPHY.md` and `CODING_STANDARDS.md` govern design decisions and
prose conventions respectively. Read them before non-trivial changes.

## License

By contributing you agree your contributions are licensed under the
project's [MIT license](LICENSE).
