import { defineRailway, github, postgres, preserve, project, service, volume } from "railway/iac";

export default defineRailway(() => {
  const Postgres = postgres("Postgres", { region: "us-east4-eqdc4a" });
  Postgres.networking = { privateNetworkEndpoint: "postgres" };
  const postgresVolume = volume("postgres-volume", { alerts: { usage: { "100": {}, "80": {}, "95": {} } }, allowOnlineResize: true, region: "us-east4-eqdc4a", sizeMB: 500 });
  const proactiveArt = service("proactive-art", {
    source: github("astro-nat/auction-scout", { checkSuites: false, rootDirectory: "/frontend" }),
    replicas: { "sfo": 1 },
    deploy: { ipv6EgressEnabled: true },
    domains: ["scout.auction"],
    env: { PORT: preserve(), VITE_API_BASE: preserve() },
  });
  const diplomaticWholeness = service("diplomatic-wholeness", {
    source: github("astro-nat/auction-scout", { checkSuites: false, rootDirectory: "/backend" }),
    replicas: { "sfo": 1 },
    env: { ANTHROPIC_API_KEY: preserve(), DATABASE_URL: preserve(), EBAY_APP_ID: preserve(), EBAY_CERT_ID: preserve(), FRONTEND_ORIGIN: preserve(), NTFY_TOPIC: preserve(), PORT: preserve(), SOLDCOMPS_API_KEY: preserve(), TARGET_ROI_PCT: preserve() },
  });

  // Background worker: same image and repo directory as the API, but it runs
  // the queue consumer instead of uvicorn. No domain — it serves no HTTP.
  // See backend/app/worker.py for why this is a separate process.
  const worker = service("worker", {
    source: github("astro-nat/auction-scout", { checkSuites: false, rootDirectory: "/backend" }),
    replicas: { "sfo": 1 },
    start: "python -m app.worker",
    env: {
      DATABASE_URL: Postgres.env.DATABASE_URL,
      // Referenced from the API service rather than copied, so there is one
      // place to rotate a key. NTFY_TOPIC matters here: the closing-soon
      // notifier moved into the worker, and without it phone alerts just
      // stop without complaining.
      ANTHROPIC_API_KEY: diplomaticWholeness.env.ANTHROPIC_API_KEY,
      SOLDCOMPS_API_KEY: diplomaticWholeness.env.SOLDCOMPS_API_KEY,
      NTFY_TOPIC: diplomaticWholeness.env.NTFY_TOPIC,
      EBAY_APP_ID: diplomaticWholeness.env.EBAY_APP_ID,
      EBAY_CERT_ID: diplomaticWholeness.env.EBAY_CERT_ID,
      TARGET_ROI_PCT: diplomaticWholeness.env.TARGET_ROI_PCT,
      // Lots worked in parallel. Down from 3 after a day of repricing spent
      // the SoldComps quota: this caps the RATE of comp lookups, though the
      // TOTAL is still lots x query variants, so it buys headroom against a
      // daily limit rather than a monthly one. Enrichment getting slower no
      // longer costs anything visible now that it runs outside the API.
      ENRICH_CONCURRENCY: "2",
      // Sold-comp requests per second, shared across those threads.
      SOLDCOMPS_RPS: "1",
    },
  });

  return project("elegant-nurturing", {
    resources: [Postgres, proactiveArt, diplomaticWholeness, worker, postgresVolume],
  });
});
