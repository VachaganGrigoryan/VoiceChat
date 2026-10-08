# Testing

## Suites

- `app/tests/unit`
  Unit tests that stub Beanie and do no I/O. They run anywhere.
- `app/tests/integration`
  Integration tests that need MongoDB and Redis.
- Rate-limit tests need Redis.

The integration fixtures connect to `mongo:27017` and `redis:6379`. Those are **compose-internal hostnames**, so these suites must run inside the Docker network. Run from the host, they time out one by one and take hours to fail.

## Running

Start the test profile once:

```bash
docker compose --profile test up -d api_test
```

Then run inside the `voicechat_api_test` container (about 500 tests in under two minutes):

```bash
docker exec voicechat_api_test sh -lc 'cd /app && pytest -q app/tests'
docker exec voicechat_api_test sh -lc 'cd /app && pytest -q app/tests/integration/test_x.py::test_y'
```

Unit tests also work from the host:

```bash
poetry run pytest app/tests/unit
```

Tests use the isolated `voicechat_test` database configured in `.env.test`.

## Known failures

- `test_dependent_models_cleanup` can fail on stale collections left in the shared test database.

## Driving the test API from a browser

The web client's Playwright suites and screenshot pipeline sign up real users against a test API. Verification emails are only delivered if something consumes the test email queue. In `.env.test` the API publishes to `email.send.test`, but the compose `worker` consumes `email.send`. Run a second, throwaway email worker for the test queue:

```bash
docker compose run -d --rm --no-deps --name voicechat_email_worker_test \
  -e EMAIL_QUEUE_NAME=email.send.test worker python -m app.workers.email_worker
```

Codes then arrive in MailHog at <http://localhost:8025>.

## OpenAPI

`openapi.json` is generated, so regenerate it after changing routes or schemas:

```bash
poetry run python app/scripts/dump_openapi.py
```

The web client keeps its own copy and verifies its API contract against it with `npm run sync:openapi`, then `npm run test:api-contract`.
