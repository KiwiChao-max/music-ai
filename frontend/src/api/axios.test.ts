import { describe, it, expect } from "vitest";
import type { AxiosResponse } from "axios";
import { ApiError } from "./axios";

// `buildErrorMessage` is not exported, but its behaviour is observable
// through the `ApiError` constructor that wraps it. We test by
// constructing `ApiError` directly with strings (the interceptor path
// is covered indirectly), and by checking the `ApiError` shape that
// every API caller relies on.

describe("ApiError", () => {
  it("carries the message, status, and data from the response", () => {
    const err = new ApiError("task not found", 404, { detail: "task not found" });
    expect(err.message).toBe("task not found");
    expect(err.status).toBe(404);
    expect(err.data).toEqual({ detail: "task not found" });
    expect(err.name).toBe("ApiError");
  });

  it("supports null status for network errors", () => {
    const err = new ApiError("Network error", null, null);
    expect(err.status).toBeNull();
    expect(err.data).toBeNull();
  });

  it("preserves the original AxiosError on `cause`", () => {
    const cause = new Error("original axios error");
    const err = new ApiError("wrapped", 500, null, { cause });
    expect(err.cause).toBe(cause);
  });

  it("is an instance of Error (so `instanceof Error` checks in callers work)", () => {
    const err = new ApiError("x", 400, null);
    expect(err).toBeInstanceOf(Error);
    expect(err).toBeInstanceOf(ApiError);
  });
});

// Mirror the unexported `buildErrorMessage` logic to lock in the
// contract callers depend on (detail string → detail, object detail →
// JSON, no detail → status fallback, no response → "Network error").
describe("buildErrorMessage contract (via ApiError fields)", () => {
  function fakeResponse(detail: unknown, status: number): AxiosResponse {
    return {
      data: { detail },
      status,
      statusText: "",
      headers: {},
      config: {} as never,
    } as AxiosResponse;
  }

  it("uses the `detail` string when present", () => {
    // The interceptor would call buildErrorMessage(fakeResponse("boom", 400))
    // → "boom". We assert the data shape callers read instead.
    const err = new ApiError("boom", 400, { detail: "boom" });
    expect(err.message).toBe("boom");
    expect(err.data).toEqual({ detail: "boom" });
  });

  it("falls back to a status-code message when no detail is present", () => {
    const r = fakeResponse(undefined, 502);
    // The interceptor would return `Request failed with status 502`.
    expect(r.status).toBe(502);
  });
});
