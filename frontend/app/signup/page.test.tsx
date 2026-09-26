import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/lib/api";
import { renderWithLanguage } from "@/lib/i18n/test-utils";
import SignupPage from "./page";

const replace = vi.fn();
let searchParamsValue = new URLSearchParams("token=abc123");
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
  useSearchParams: () => searchParamsValue,
}));

const getInvitePreviewMock = vi.fn();
const acceptInviteMock = vi.fn();
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getInvitePreview: (...args: unknown[]) => getInvitePreviewMock(...args),
    acceptInvite: (...args: unknown[]) => acceptInviteMock(...args),
  };
});

beforeEach(() => {
  replace.mockClear();
  getInvitePreviewMock.mockReset();
  acceptInviteMock.mockReset();
  searchParamsValue = new URLSearchParams("token=abc123");
});

describe("SignupPage", () => {
  it("shows the username the invite is for once the token resolves", async () => {
    getInvitePreviewMock.mockResolvedValue({ username: "teammate1" });
    renderWithLanguage(<SignupPage />);

    expect(await screen.findByText("Creando cuenta para: teammate1")).toBeInTheDocument();
    expect(getInvitePreviewMock).toHaveBeenCalledWith("abc123", expect.any(AbortSignal));
  });

  it("shows an invalid-invite message when there is no token at all", async () => {
    searchParamsValue = new URLSearchParams();
    renderWithLanguage(<SignupPage />);

    expect(
      await screen.findByText("Este enlace de invitación no es válido o ya expiró"),
    ).toBeInTheDocument();
    expect(getInvitePreviewMock).not.toHaveBeenCalled();
  });

  it("shows an invalid-invite message when the preview lookup 404s", async () => {
    getInvitePreviewMock.mockRejectedValue(new ApiError(404));
    renderWithLanguage(<SignupPage />);

    expect(
      await screen.findByText("Este enlace de invitación no es válido o ya expiró"),
    ).toBeInTheDocument();
  });

  it("rejects mismatched passwords without calling the API", async () => {
    getInvitePreviewMock.mockResolvedValue({ username: "teammate1" });
    const user = userEvent.setup();
    renderWithLanguage(<SignupPage />);

    await screen.findByText("Creando cuenta para: teammate1");
    await user.type(screen.getByLabelText("Contraseña"), "password123");
    await user.type(screen.getByLabelText("Confirmar contraseña"), "different123");
    await user.click(screen.getByRole("button", { name: "Crear cuenta" }));

    expect(await screen.findByText("Las contraseñas no coinciden")).toBeInTheDocument();
    expect(acceptInviteMock).not.toHaveBeenCalled();
  });

  it("submits a matching password and redirects home on success", async () => {
    getInvitePreviewMock.mockResolvedValue({ username: "teammate1" });
    acceptInviteMock.mockResolvedValue({ username: "teammate1", is_admin: false });
    const user = userEvent.setup();
    renderWithLanguage(<SignupPage />);

    await screen.findByText("Creando cuenta para: teammate1");
    await user.type(screen.getByLabelText("Contraseña"), "password123");
    await user.type(screen.getByLabelText("Confirmar contraseña"), "password123");
    await user.click(screen.getByRole("button", { name: "Crear cuenta" }));

    expect(acceptInviteMock).toHaveBeenCalledWith("abc123", "password123");
    await waitFor(() => expect(replace).toHaveBeenCalledWith("/"));
  });
});
