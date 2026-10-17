
const FE = "C:/Users/leste/Documents/Convexa-wt-hmfe/frontend";
export default ({
  root: "C:/Users/leste/AppData/Local/Temp/claude/C--Users-leste-Documents-Convexa/86074854-c3c4-4439-ab1c-6304ceadc777/scratchpad/hmrender",
  resolve: { alias: { "@": FE, "lightweight-charts": FE + "/node_modules/lightweight-charts/dist/lightweight-charts.production.mjs" } },
  build: { outDir: "out", emptyOutDir: true, minify: false, lib: { entry: "entry.ts", name: "hmrender", formats: ["iife"], fileName: () => "bundle.js" } },
  logLevel: "warn",
});
