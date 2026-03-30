import { defineConfig } from "vite";

export default defineConfig({
  build: {
    emptyOutDir: true,
    outDir: "website/assets/client",
    rollupOptions: {
      input: "frontend/rankings-app.ts",
      output: {
        entryFileNames: "rankings-app.js",
        assetFileNames: (assetInfo) =>
          assetInfo.name?.endsWith(".css") ? "rankings-app.css" : "[name][extname]",
      },
    },
  },
});

