import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const replace = vi.fn();

function stubLocation(pathname: string) {
  vi.stubGlobal("location", { pathname, replace });
  Object.defineProperty(window, "location", { value: { pathname, replace }, writable: true, configurable: true });
}

beforeEach(async () => {
  replace.mockReset();
  const { resetSessionRedirectForTests } = await import("./session-redirect");
  resetSessionRedirectForTests();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("redirectToLoginOnUnauthorized", () => {
  it("sends a gated page to /login, only once", async () => {
    stubLocation("/");
    const { redirectToLoginOnUnauthorized } = await import("./session-redirect");
    redirectToLoginOnUnauthorized("/gamma/SPX");
    redirectToLoginOnUnauthorized("/market/SPX");
    expect(replace).toHaveBeenCalledTimes(1);
    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("does nothing on the login and signup pages", async () => {
    const { redirectToLoginOnUnauthorized } = await import("./session-redirect");
    stubLocation("/login");
    redirectToLoginOnUnauthorized("/auth/me");
    stubLocation("/signup/abc123");
    redirectToLoginOnUnauthorized("/auth/invites/abc123");
    expect(replace).not.toHaveBeenCalled();
  });

  it("ignores the 401s that are an expected answer (wrong password, bad invite, logout)", async () => {
    stubLocation("/");
    const { redirectToLoginOnUnauthorized, isExpectedUnauthorized } = await import("./session-redirect");
    for (const path of ["/auth/login", "/auth/logout", "/auth/accept-invite", "/auth/invites/x"]) {
      expect(isExpectedUnauthorized(path)).toBe(true);
      redirectToLoginOnUnauthorized(path);
    }
    expect(replace).not.toHaveBeenCalled();
  });
});
