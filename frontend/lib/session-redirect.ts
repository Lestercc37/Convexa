// A 401 from the API in the middle of a session (the cookie expired, or was cleared) used to leave the dashboard on "The request couldn't be completed" because the auth gate only checks the session
// once, when the page opens. Any 401 from a gated API call now sends the browser to the login page, once.

const PUBLIC_PATH_PREFIXES = ["/login", "/signup"];
// Requests whose 401 is an expected answer, not a lost session: a wrong password, a bad invite, an already-cleared session at logout.
const EXPECTED_401_API_PATHS = ["/auth/login", "/auth/logout", "/auth/accept-invite", "/auth/invites"];

let redirecting = false;

export function isExpectedUnauthorized(apiPath: string): boolean {
  return EXPECTED_401_API_PATHS.some((prefix) => apiPath.startsWith(prefix));
}

export function redirectToLoginOnUnauthorized(apiPath: string): void {
  if (typeof window === "undefined" || redirecting || isExpectedUnauthorized(apiPath)) return;
  if (PUBLIC_PATH_PREFIXES.some((prefix) => window.location.pathname.startsWith(prefix))) return;
  redirecting = true;
  // A /vista/... window (desktop app) comes back to its own view after the login.
  const here = `${window.location.pathname}${window.location.search}`;
  window.location.replace(here.startsWith("/vista/") ? `/login?next=${encodeURIComponent(here)}` : "/login");
}

// Test hook: the module keeps "already redirecting" for the life of the page, which is what production wants.
export function resetSessionRedirectForTests(): void {
  redirecting = false;
}
