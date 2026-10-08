# Health

Base path: `/health`

## Endpoints

- `GET /health/live`
  Liveness. Returns 200 while the process is serving requests. The Docker healthcheck uses this.
- `GET /health/ready`
  Readiness. Returns 200 only when required dependencies are healthy.

## Notes

- Neither endpoint requires authentication.
