"""End-to-end tests for Lerim CLI.

E2E tests validate complete user journeys through the CLI.
Unlike integration tests (which call Python APIs directly),
E2E tests invoke the CLI binary via subprocess and start
a real server to test the full stack.
"""
