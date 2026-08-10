import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement } from "react";
import { LanguageProvider } from "./language-context";

// Every component under test now calls `useLanguage()`, which throws
// outside a `LanguageProvider` — wrap instead of repeating the provider
// boilerplate in every test file. Uses RTL's `wrapper` option (not manual
// JSX wrapping) specifically so the returned `rerender()` re-applies the
// same wrapper automatically — tests that call `rerender(<Component .../>)`
// directly would otherwise unmount the provider on the first rerender.
export function renderWithLanguage(ui: ReactElement): RenderResult {
  return render(ui, { wrapper: LanguageProvider });
}
