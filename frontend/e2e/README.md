# UI smoke test

A Playwright walk-through of every page against a running stack: login, import tabs, search, map (select, AI suggest, auto-map settings, confirm), review, commit, settings, users, task tray, mobile layout and logout. It exits with status 1 when a step fails or when it records page errors, console errors or 5xx responses. Note that it changes data (it confirms a mapping), so run it against a test stack. Screenshots go to `./shots`.

The stack needs data (vocabulary, embeddings, at least two unmapped source concepts), e.g. after running the steps in the README with the fake LLM gateway.

```bash
cd frontend/e2e
echo '{ "type": "module", "dependencies": { "playwright": "1.63.0" } }' > package.json
docker run --rm --network host --ipc=host -e ADMIN_PASSWORD=... -e BASE=https://localhost \
  -v $PWD:/work -w /work mcr.microsoft.com/playwright:v1.63.0-noble sh -c "npm i --silent && node smoke.mjs"
```
