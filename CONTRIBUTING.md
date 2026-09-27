# Contributing to Sideword Server

Thanks for helping improve Sideword Server. The project welcomes focused bug
fixes, tests, documentation, and well-scoped feature proposals.

## Before you start

- Search existing issues before opening a new one.
- Use an issue to discuss significant API, storage, or protocol changes first.
- Do not open public issues for vulnerabilities; follow `SECURITY.md`.
- Keep pull requests focused. Unrelated refactors should be separate changes.

## Development setup

Python 3.12 or newer is supported.

```bash
git clone https://github.com/YOUR-USER/sideword-chat-server.git
cd sideword-chat-server
git switch -c feature/your-change
python -m venv .venv
```

Activate the environment and install development dependencies:

```bash
# Linux/macOS
source .venv/bin/activate

# Windows PowerShell
.venv\Scripts\Activate.ps1

python -m pip install -r requirements-dev.txt
```

Create local configuration:

```bash
cp .env.example .env
python -c "import secrets; print(secrets.token_urlsafe(64))"
```

Put the generated value in `SIDEWORD_SECRET_KEY`, then update the local admin
credentials in `.env`. Never commit `.env`, databases, exports, or credentials.

## Quality checks

Run the same checks used by CI:

```bash
python -m pip check
python -m ruff check .
python -m compileall -q app scripts tests
python -m pytest
node --check app/static/client.js
node --check app/static/link-form.js
node --check app/static/admin-links.js
node --check app/static/invite-vault.js
node --test tests/client_delivery.test.cjs tests/admin_links.test.cjs tests/invite_vault.test.cjs
```

For changes affecting deployment, also build the image:

```bash
docker build -t sideword-chat-server:test .
```

## Pull requests

1. Fork the repository and create a feature or fix branch. Do not work directly on `main`.
2. Add tests for behavioral changes.
3. Update documentation when configuration or API behavior changes.
4. Run all quality checks.
5. Open a pull request explaining the problem, solution, and validation.
6. For releases, update `app/version.py` and `CHANGELOG.md`. Merge into `main` only after all checks pass for the exact commit.

By contributing, you agree that your contribution is licensed under the MIT
License covering this repository.
