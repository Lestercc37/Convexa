import { AuthGate } from "@/components/auth-gate";
import { Dashboard } from "@/components/dashboard";

export default function Home() {
  return (
    <AuthGate>
      <Dashboard />
    </AuthGate>
  );
}
