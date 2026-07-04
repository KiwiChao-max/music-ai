import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

// Unmount any components mounted by render() after each test so the
// jsdom document doesn't leak DOM nodes between tests.
afterEach(() => {
  cleanup();
});
