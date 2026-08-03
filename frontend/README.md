# Convexa Frontend

Dashboard web de Convexa construido con Next.js App Router y TypeScript.

## Desarrollo local

Con el backend ejecutándose en `http://localhost:8000`:

```powershell
Copy-Item .env.example .env.local
pnpm install
pnpm dev
```

Abre `http://localhost:3000`.

Para usar otra instancia del backend, cambia `CONVEXA_API_URL` en `.env.local`.

## Validación

```powershell
pnpm test
pnpm lint
pnpm build
```
