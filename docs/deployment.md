# Deployment

The repository is one monorepo, but each service should deploy independently.

Recommended deployment units:

- `web`
- `services/main-server`
- `services/agent-worker`

GitHub Actions should use path filters so frontend changes do not redeploy backend services.
