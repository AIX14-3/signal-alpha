# Web

Frontend application for Signal Alpha.

This service should call the main API server rather than calling the agent worker directly.

## Local Development

```bash
npm ci
npm run dev
```

Initial dashboard:

```text
http://localhost:3000
```

## Reproducible Verification

Use the same dependency install and verification contract as CI:

```bash
npm ci
npm run verify
```

`npm run verify` runs the frontend typecheck, production build, source-level test suite, and
render smoke test in sequence. The render smoke test starts the built Next.js app and checks
core public user-flow routes.
