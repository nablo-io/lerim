# Quickstart

This is the shortest real path.

The open-source quickstart uses the sources available in the current package.
Customer deployments can adapt trace imports around a specific business workflow
such as support escalations, research briefs, incidents, reviews, or revenue
handoffs.

## 1. Prepare

```bash
lerim init
lerim connect auto
lerim project add .
```

## 2. Start the service

```bash
lerim up
```

## 3. Check status

```bash
lerim status
lerim status --live
```

## 4. Run the flows

```bash
lerim ingest
lerim curate
lerim answer "What sources supported our last competitor-pricing assumption?"
```

## 5. Know where data lives

Global Lerim state includes:

- the durable context store
- the session catalog and work queue
- workspace artifacts for ingest, curate, answers, and context briefs
