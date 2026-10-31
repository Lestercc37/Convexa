import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { AuthGate } from "./auth-gate";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

const getMeMock = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, getMe: (...args: unknown[]) => getMeMock(...args) };
});

beforeEach(() => {
  replace.mockClear();
  getMeMock.mockReset();
});

describe("AuthGate", () => {
  it("renders children once the session check succeeds", async () => {
    getMeMock.mockResolvedValue({ username: "lester", is_admin: true });

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    expect(await screen.findByText("protected content")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("sends the desktop app from the web dashboard (/) to its chart view", async () => {
    getMeMock.mockResolvedValue({ username: "lester", is_admin: true });
    const agent = vi.spyOn(navigator, "userAgent", "get").mockReturnValue("Mozilla/5.0 Chrome/140.0 Electron/38.0.0 Safari/537.36");
    window.history.replaceState(null, "", "/");

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/vista/grafico"));
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
    agent.mockRestore();
  });

  it("redirects to /login and never renders children when there is no session", async () => {
    getMeMock.mockRejectedValue(new ApiError(401));

    render(
      <AuthGate>
        <p>protected content</p>
      </AuthGate>,
    );

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/login"));
    expect(screen.queryByText("protected content")).not.toBeInTheDocument();
  });
});
