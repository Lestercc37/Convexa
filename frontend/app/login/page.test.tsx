import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import LoginPage from "./page";

const replace = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

const loginMock = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return { ...actual, login: (...args: unknown[]) => loginMock(...args) };
});

beforeEach(() => {
  replace.mockClear();
  loginMock.mockReset();
  window.localStorage.clear();
});

describe("LoginPage", () => {
  it("submits the entered credentials and redirects home on success", async () => {
    loginMock.mockResolvedValue({ username: "lester", is_admin: true });
    const user = userEvent.setup();
    renderWithLanguage(<LoginPage />);

    await user.type(screen.getByLabelText("Usuario"), "lester");
    await user.type(screen.getByLabelText("Contraseña"), "hunter2");
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    expect(loginMock).toHaveBeenCalledWith("lester", "hunter2");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });

  it("shows an invalid-credentials message on a 401 and does not redirect", async () => {
    loginMock.mockRejectedValue(new ApiError(401));
    const user = userEvent.setup();
    renderWithLanguage(<LoginPage />);

    await user.type(screen.getByLabelText("Usuario"), "lester");
    await user.type(screen.getByLabelText("Contraseña"), "wrong");
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByText("Usuario o contraseña incorrectos")).toBeInTheDocument();
    expect(replace).not.toHaveBeenCalled();
  });

  it("remembers the username after a login and fills it in next time", async () => {
    loginMock.mockResolvedValue({ username: "gabriel95", is_admin: false });
    const user = userEvent.setup();
    const first = renderWithLanguage(<LoginPage />);
    await user.type(screen.getByLabelText("Usuario"), "gabriel95");
    await user.type(screen.getByLabelText("Contraseña"), "a password");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    await waitFor(() => expect(replace).toHaveBeenCalled());
    first.unmount();

    renderWithLanguage(<LoginPage />);
    expect(screen.getByLabelText("Usuario")).toHaveValue("gabriel95");
    expect(screen.getByLabelText("Contraseña")).toHaveFocus();
    // Only the username is ever kept, never the password.
    expect(JSON.stringify(window.localStorage)).not.toContain("a password");
  });

  it("forgets the username when 'Recordar usuario' is unticked", async () => {
    window.localStorage.setItem("convexa.rememberedUsername", "gabriel95");
    loginMock.mockResolvedValue({ username: "gabriel95", is_admin: false });
    const user = userEvent.setup();
    renderWithLanguage(<LoginPage />);
    await user.click(screen.getByLabelText("Recordar usuario"));
    await user.type(screen.getByLabelText("Contraseña"), "a password");
    await user.click(screen.getByRole("button", { name: "Entrar" }));
    await waitFor(() => expect(replace).toHaveBeenCalled());
    expect(window.localStorage.getItem("convexa.rememberedUsername")).toBeNull();
  });

  it("shows a generic error message on a non-401 failure", async () => {
    loginMock.mockRejectedValue(new ApiError(500));
    const user = userEvent.setup();
    renderWithLanguage(<LoginPage />);

    await user.type(screen.getByLabelText("Usuario"), "lester");
    await user.type(screen.getByLabelText("Contraseña"), "whatever");
    await user.click(screen.getByRole("button", { name: "Entrar" }));

    expect(await screen.findByText("No se pudo iniciar sesión, intenta de nuevo")).toBeInTheDocument();
  });
});
